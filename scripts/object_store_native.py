"""Isolated loopback SeaweedFS 4.48 with worktree-owned data and credential files.

prepare verifies the official full Windows release archive before extracting it.
No caller-supplied executable, remote process or shared tool cache is modified.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import time
import zipfile

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / '.runtime/object-store'
VERSION = '4.48'
SHA256 = 'fe90c04c0620ad1a1c756f86cd5e1443773f56a446688077a4bb5ec04c3cc874'
PORTS = dict(master=29333, volume=28081, filer=28888, s3=28333)


def ownership():
    marker = json.loads((STATE/'ownership.json').read_text())
    if marker['worktree'] != str(ROOT.resolve()) or marker['state'] != str(STATE.resolve()):
        raise RuntimeError('Object store ownership mismatch')
    return marker


def stop():
    import psutil
    marker = ownership()
    pidfile = STATE/'server.pid'
    if not pidfile.exists():
        return
    try:
        process = psutil.Process(int(pidfile.read_text()))
        if Path(process.exe()).resolve() != Path(marker['executable']).resolve() or Path(process.cwd()).resolve() != STATE.resolve():
            raise RuntimeError('Refusing to stop unowned process')
        process.terminate()
        process.wait(timeout=30)
    except psutil.NoSuchProcess:
        pass
    pidfile.unlink(missing_ok=True)


def start():
    marker = ownership()
    if (STATE/'server.pid').exists():
        raise RuntimeError('PID file exists; use restart or stop')
    for port in list(PORTS.values()) + [PORTS[k]+10000 for k in PORTS]:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',port))
    command = [marker['executable'], 'server', '-ip=127.0.0.1', '-ip.bind=127.0.0.1',
        '-dir='+str(STATE/'data'), '-master.port='+str(PORTS['master']),
        '-volume.port='+str(PORTS['volume']), '-volume.max=8', '-master.volumeSizeLimitMB=64',
        '-master.telemetry=false', '-s3.port.iceberg=0', '-s3.port.lance=0', '-filer',
        '-filer.port='+str(PORTS['filer']), '-s3', '-s3.port='+str(PORTS['s3']),
        '-s3.config='+str(STATE/'s3.json')]
    with (STATE/'server.log').open('ab') as log:
        process = subprocess.Popen(command, cwd=STATE, stdout=log, stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
    (STATE/'server.pid').write_text(str(process.pid))
    import boto3
    from botocore.config import Config
    from botocore.exceptions import BotoCoreError, ClientError
    admin = json.loads((STATE/'admin.json').read_text())
    client = boto3.client('s3', endpoint_url=f"http://127.0.0.1:{PORTS['s3']}",
        aws_access_key_id=admin['access'], aws_secret_access_key=admin['secret'],
        config=Config(connect_timeout=1, read_timeout=2,retries={'max_attempts':0}))
    deadline = time.monotonic()+90
    while time.monotonic()<deadline:
        if process.poll() is not None:
            raise RuntimeError('SeaweedFS exited; inspect owned runtime log')
        try:
            client.list_buckets()
            try:
                client.head_bucket(Bucket='lzo-artifacts')
            except ClientError:
                client.create_bucket(Bucket='lzo-artifacts')
            key = '_readiness/'+secrets.token_hex(12)
            client.put_object(Bucket='lzo-artifacts',Key=key,Body=b'ready')
            body = client.get_object(Bucket='lzo-artifacts',Key=key)['Body']
            try:
                if body.read()!=b'ready':
                    raise RuntimeError('S3 readiness content mismatch')
            finally:
                body.close()
            client.delete_object(Bucket='lzo-artifacts',Key=key)
            print('SeaweedFS 4.48 private S3 ready on loopback port '+str(PORTS['s3']))
            return
        except (BotoCoreError, ClientError):
            time.sleep(.25)
    raise RuntimeError('Private S3 readiness timeout')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['prepare','start','stop','restart'])
    parser.add_argument('--archive', type=Path)
    args = parser.parse_args()
    STATE.mkdir(parents=True,exist_ok=True)
    if args.action=='prepare':
        if (STATE/'ownership.json').exists():
            raise RuntimeError('Already provisioned; preserve credential identity')
        if args.archive is None or hashlib.file_digest(args.archive.open('rb'),'sha256').hexdigest()!=SHA256:
            raise RuntimeError('Official full Windows 4.48 archive with verified SHA-256 required')
        with zipfile.ZipFile(args.archive) as archive:
            archive.extractall(STATE/'bin')
        executable = STATE/'bin/weed.exe'
        if not executable.exists():
            raise RuntimeError('Full release weed.exe absent')
        (STATE/'data').mkdir(exist_ok=True)
        access, secret = secrets.token_hex(16), secrets.token_hex(32)
        admin = dict(access=secrets.token_hex(16),secret=secrets.token_hex(32))
        (STATE/'access-key').write_text(access)
        (STATE/'secret-key').write_text(secret)
        (STATE/'admin.json').write_text(json.dumps(admin))
        (STATE/'s3.json').write_text(json.dumps({'identities':[
            {'name':'bootstrap','credentials':[{'accessKey':admin['access'],'secretKey':admin['secret']}], 'actions':['Admin']},
            {'name':'lzo','credentials':[{'accessKey':access,'secretKey':secret}],
             'actions':['Read:lzo-artifacts','Write:lzo-artifacts','List:lzo-artifacts']}
        ]}))
        (STATE/'filer.toml').write_text('[leveldb2]\nenabled = true\ndir = "./filerldb2"\n')
        (STATE/'ownership.json').write_text(json.dumps(dict(worktree=str(ROOT.resolve()),
            state=str(STATE.resolve()),executable=str(executable.resolve()),version=VERSION,archive_sha256=SHA256)))
        env = dict(LZO_BLOB_PROVIDER='s3',LZO_S3_ENDPOINT=f"http://127.0.0.1:{PORTS['s3']}",
            LZO_S3_BUCKET='lzo-artifacts', LZO_S3_ACCESS_KEY_FILE=str((STATE/'access-key').resolve()),
            LZO_S3_SECRET_KEY_FILE=str((STATE/'secret-key').resolve()),LZO_BLOB_STAGING_PATH=str((STATE/'staging').resolve()))
        (STATE/'services.env').write_text(''.join(f'{k}={v}\n' for k,v in env.items()))
        print('Verified official SeaweedFS archive and generated private credentials')
    elif args.action=='stop':
        stop()
    else:
        if args.action=='restart':
            stop()
        start()


if __name__=='__main__':
    main()
