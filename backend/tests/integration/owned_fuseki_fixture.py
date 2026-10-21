"""Owned, bounded Fuseki fault injection; never control the native shared service."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import time
import zipfile
from datetime import datetime,timezone

import httpx
import psutil
import pytest


@pytest.fixture
def owned_fuseki(tmp_path):
    root=Path(__file__).resolve().parents[3]
    jar=Path(os.environ.get("LZO_TEST_FUSEKI_JAR",root/".tools/fuseki/apache-jena-fuseki-6.2.0/fuseki-server.jar")).resolve()
    java_value=os.environ.get("LZO_TEST_JAVA")
    if not java_value and os.name=="nt":
        homes=[Path(os.environ[k]) for k in ("LZO_JAVA_HOME","JAVA_HOME") if os.environ.get(k)]
        homes+=sorted(Path("C:/Program Files/JetBrains").glob("PyCharm*/jbr"),reverse=True)
        java_value=next((str(p/"bin/java.exe") for p in homes if (p/"bin/java.exe").is_file()),None)
    if not java_value:pytest.fail("LZO_TEST_JAVA must identify the existing Java 21 executable")
    java=Path(java_value).resolve()
    assert jar.is_file() and java.is_file(),"Existing pinned Fuseki/Java fixture inputs required"
    archive=root/".tools/apache-jena-fuseki-6.2.0.zip"
    jar_hash=hashlib.sha256(jar.read_bytes()).hexdigest()
    if archive.is_file():
        lock=json.loads((root/"infra/versions.lock.json").read_text())
        expected="0c3e2dc55037ced325e3716bf86e91fdf20fb96e595fc3530232c05fae789a9e"
        assert hashlib.sha256(archive.read_bytes()).hexdigest()==expected
        with zipfile.ZipFile(archive) as package:
            assert hashlib.sha256(package.read("apache-jena-fuseki-6.2.0/fuseki-server.jar")).hexdigest()==jar_hash
    else:
        assert os.environ.get("LZO_TEST_FUSEKI_JAR_SHA256")==jar_hash,"Portable pinned JAR hash required"
    env={k:os.environ[k] for k in ("SystemRoot","WINDIR","TEMP","TMP","HOME","LANG") if k in os.environ}
    env["PATH"]=str(java.parent)
    version=subprocess.run([str(java),"-version"],env=env,capture_output=True,text=True,timeout=10)
    assert version.returncode==0 and 'version "21' in version.stderr
    scratch=(tmp_path/"owned-fuseki").resolve();scratch.mkdir()
    data=scratch/"tdb2";data.mkdir()
    config=scratch/"config.ttl"
    config.write_text((root/"database/fuseki/config.ttl").read_text().replace('"databases/lzo"',json.dumps(data.as_posix())),encoding="utf-8")
    env.update(FUSEKI_BASE=str(scratch),FUSEKI_HOME=str(jar.parent))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1",0));port=listener.getsockname()[1]
    assert port!=13030
    endpoint=f"http://127.0.0.1:{port}/lzo"
    evidence=[];evidence_path=scratch/"ownership.json"

    class Owned:
        process=None
        identity=None
        url=endpoint
        def record(self,phase,**extra):
            evidence.append({"phase":phase,"at_utc":datetime.now(timezone.utc).isoformat(),"endpoint":endpoint,"port":port,"tdb2":str(data),
                "jar_sha256":jar_hash,"java_version":version.stderr.splitlines()[0],"identity":self.identity,**extra})
            evidence_path.write_text(json.dumps(evidence,indent=2),encoding="utf-8")
        def verify(self):
            assert self.process and self.process.poll() is None
            current=psutil.Process(self.process.pid)
            actual={"pid":current.pid,"created":current.create_time(),"exe":current.exe(),"cmd":current.cmdline(),"cwd":current.cwd()}
            assert actual==self.identity,"Owned Java incarnation changed"
            return current
        def start(self):
            assert self.process is None or self.process.poll() is not None
            command=[str(java),"-Xmx512m","-jar",str(jar),f"--config={config}","--localhost",f"--port={port}"]
            with (scratch/"java.log").open("ab") as output:
                self.process=subprocess.Popen(command,cwd=scratch,env=env,stdout=output,stderr=subprocess.STDOUT,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0)
            current=psutil.Process(self.process.pid)
            self.identity={"pid":current.pid,"created":current.create_time(),"exe":current.exe(),"cmd":current.cmdline(),"cwd":current.cwd()}
            assert Path(self.identity["exe"]).resolve()==java and Path(self.identity["cwd"]).resolve()==scratch
            self.record("launch")
            deadline=time.monotonic()+40
            while time.monotonic()<deadline:
                current=self.verify()
                listeners=[c for c in current.net_connections(kind="inet") if c.status==psutil.CONN_LISTEN and c.laddr.port==port]
                if listeners:
                    assert all(c.laddr.ip in ("127.0.0.1","::1") for c in listeners)
                    # Process-owned loopback listener is proven before HTTP.
                    try:
                        response=httpx.get(endpoint+"/query",params={"query":"ASK {}"},timeout=1,trust_env=False)
                        if response.status_code==200:
                            self.record("healthy",listener_addresses=[c.laddr.ip for c in listeners]);return
                    except httpx.TransportError:pass
                time.sleep(.1)
            raise AssertionError("Owned Fuseki did not become healthy within 40 seconds")
        def stop(self,phase="abrupt_outage"):
            if self.process is None:return
            if self.process.poll() is None:
                self.verify();self.record(phase)
                self.process.terminate()
                try:self.process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.verify();self.record("forced_kill");self.process.kill();self.process.wait(timeout=10)
            with socket.socket() as probe:
                probe.settimeout(1)
                closed=probe.connect_ex(("127.0.0.1",port))!=0
            self.record("exited",exit_code=self.process.returncode,port_closed=closed)
            assert closed,"Owned port remained open after process exit"
            assert not psutil.pid_exists(self.process.pid),"Owned PID remained after wait"
    owned=Owned()
    try:
        owned.start()
        yield owned
    finally:
        owned.stop("cleanup")
        print("owned_fuseki_evidence="+str(evidence_path))
