"""This is process-level egress isolation, not a disconnected deployment claim."""
import socket
import subprocess
import sys

import pytest


def test_offline_guard_denies_external_dns_and_inherited_child():
    with pytest.raises(OSError, match='denies'):
        socket.getaddrinfo('example.invalid', 443)
    child = subprocess.run([sys.executable, '-c', "import socket; socket.create_connection(('203.0.113.1',443),timeout=1)"],
        capture_output=True, text=True)
    assert child.returncode != 0 and 'denies' in child.stderr
