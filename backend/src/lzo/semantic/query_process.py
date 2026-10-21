"""Owned query process, bounded pipes, active cancellation, deadline and RSS.

Application confinement, not an OS sandbox. Never supervises unrelated processes.
"""
import json
import errno
import os
from pathlib import Path
import subprocess
import sys
import time
from threading import Event, Thread

import psutil

from lzo.errors import ApiError


def _windows_memory_job(process, memory_mb):
    """Attach only this newly owned child; cap committed virtual memory."""
    import ctypes as c
    from ctypes import wintypes as w
    class Basic(c.Structure):
        _fields_ = [("PerProcessUserTimeLimit", c.c_int64), ("PerJobUserTimeLimit", c.c_int64),
            ("LimitFlags", w.DWORD), ("MinimumWorkingSetSize", c.c_size_t),
            ("MaximumWorkingSetSize", c.c_size_t), ("ActiveProcessLimit", w.DWORD),
            ("Affinity", c.c_size_t), ("PriorityClass", w.DWORD), ("SchedulingClass", w.DWORD)]
    class IO(c.Structure):
        _fields_ = [(name, c.c_uint64) for name in ("ReadOperationCount", "WriteOperationCount",
            "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]
    class Extended(c.Structure):
        _fields_ = [("BasicLimitInformation", Basic), ("IoInfo", IO),
            ("ProcessMemoryLimit", c.c_size_t), ("JobMemoryLimit", c.c_size_t),
            ("PeakProcessMemoryUsed", c.c_size_t), ("PeakJobMemoryUsed", c.c_size_t)]
    kernel = c.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = [c.c_void_p, w.LPCWSTR], w.HANDLE
    kernel.SetInformationJobObject.argtypes = [w.HANDLE, c.c_int, c.c_void_p, w.DWORD]
    kernel.SetInformationJobObject.restype = w.BOOL
    kernel.AssignProcessToJobObject.argtypes, kernel.AssignProcessToJobObject.restype = [w.HANDLE, w.HANDLE], w.BOOL
    kernel.CloseHandle.argtypes, kernel.CloseHandle.restype = [w.HANDLE], w.BOOL
    job = kernel.CreateJobObjectW(None, None)
    if not job:
        raise ApiError("SPARQL_CAPABILITY_UNAVAILABLE", 503, "Hard query memory limit is unavailable")
    try:
        info = Extended()
        info.BasicLimitInformation.LimitFlags = 0x100 | 0x2000  # PROCESS_MEMORY | KILL_ON_JOB_CLOSE
        info.ProcessMemoryLimit = memory_mb * 1024 * 1024
        if not kernel.SetInformationJobObject(job, 9, c.byref(info), c.sizeof(info)):
            raise OSError()
        if not kernel.AssignProcessToJobObject(job, w.HANDLE(int(process._handle))):
            raise OSError()
    except OSError:
        kernel.CloseHandle(job)
        raise ApiError("SPARQL_CAPABILITY_UNAVAILABLE", 503, "Hard query memory limit is unavailable") from None
    return lambda: kernel.CloseHandle(job)


def run_child(payload, *, timeout_ms, memory_mb, input_bytes, output_bytes, cancelled=None,
              module="lzo.semantic.query_child"):
    if module not in {"lzo.semantic.query_child", "lzo.ingestion.files"}:
        raise ValueError("Unregistered bounded child module")
    started = time.monotonic()
    encoded = bytearray()
    for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(payload):
        if cancelled is not None and cancelled():
            raise ApiError("SPARQL_CANCELLED", 422, "Query execution did not complete")
        if (time.monotonic()-started)*1000 >= timeout_ms:
            raise ApiError("SPARQL_TIMEOUT", 422, "Query execution did not complete")
        value = chunk.encode("utf-8")
        if len(encoded) + len(value) > input_bytes:
            raise ApiError("SPARQL_BUDGET_EXCEEDED", 422, "Query input exceeds budget")
        encoded.extend(value)
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"SYSTEMROOT", "WINDIR", "TEMP", "TMP", "PATH", "LANG"}}
    environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]), PYTHONIOENCODING="utf-8",
                       LZO_QUERY_CHILD_MEMORY_BYTES=str(memory_mb*1024*1024))
    process = subprocess.Popen([sys.executable, "-m", module],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    close_job = None
    try:
        # No query input is sent until the hard limit has attached. Linux sets
        # RLIMIT_AS in the fixed child bootstrap before reading input.
        if os.name == "nt":
            close_job = _windows_memory_job(process, memory_mb)
        elif not sys.platform.startswith("linux"):
            raise ApiError("SPARQL_CAPABILITY_UNAVAILABLE", 503, "Hard query memory limit is unavailable")
    except BaseException:
        if process.poll() is None:
            process.kill()
        process.wait()
        for pipe in (process.stdin, process.stdout, process.stderr):
            pipe.close()
        raise
    overflow = Event()
    stopping, transfer_failed = Event(), Event()
    output, errors = bytearray(), bytearray()

    def pipe_error(error):
        expected = (error.errno in {errno.EPIPE, errno.EINVAL, errno.EBADF}
                    and (stopping.is_set() or process.poll() is not None))
        if not expected:
            transfer_failed.set()

    def read(pipe, target, limit):
        try:
            while chunk := pipe.read(4096):
                if len(target) + len(chunk) > limit:
                    overflow.set()
                    return
                target.extend(chunk)
        finally:
            pipe.close()

    def write():
        try:
            for offset in range(0, len(encoded), 4096):
                process.stdin.write(encoded[offset:offset+4096])
            process.stdin.flush()
        except OSError as error:
            pipe_error(error)
        finally:
            try:
                process.stdin.close()
            except OSError as error:
                # Closing a buffered pipe can flush bytes after cancellation
                # killed this owned child. The failed transfer is already final.
                pipe_error(error)

    threads = [Thread(target=read, args=(process.stdout, output, output_bytes), daemon=True),
               Thread(target=read, args=(process.stderr, errors, 8192), daemon=True),
               Thread(target=write, daemon=True)]
    monitor = psutil.Process(process.pid)
    for thread in threads:
        thread.start()
    code = None
    try:
        while True:
            if cancelled is not None and cancelled():
                code = "SPARQL_CANCELLED"
            elif overflow.is_set():
                code = "SPARQL_BUDGET_EXCEEDED"
            elif transfer_failed.is_set():
                code = "SPARQL_EXECUTION_INCOMPLETE"
            elif (time.monotonic() - started) * 1000 >= timeout_ms:
                code = "SPARQL_TIMEOUT"
            else:
                try:
                    if monitor.memory_info().rss > memory_mb * 1024 * 1024:
                        code = "SPARQL_MEMORY_EXCEEDED"
                except psutil.NoSuchProcess:
                    pass
            if code or process.poll() is not None:
                break
            time.sleep(.01)
    finally:
        stopping.set()
        if process.poll() is None:
            process.kill()
        process.wait()
        for thread in threads:
            thread.join()
        if close_job is not None:
            close_job()
    if transfer_failed.is_set() and code is None:
        code = "SPARQL_EXECUTION_INCOMPLETE"
    if code or overflow.is_set():
        raise ApiError(code or "SPARQL_BUDGET_EXCEEDED", 422, "Query execution did not complete")
    try:
        result = json.loads(output)
        if process.returncode != 0 or not isinstance(result, dict):
            raise ValueError()
    except (ValueError, UnicodeDecodeError):
        raise ApiError("SPARQL_EXECUTION_INCOMPLETE", 422, "Query execution did not complete") from None
    if "error" in result:
        raise ApiError(result["error"], 422, "Query execution did not complete")
    return result
