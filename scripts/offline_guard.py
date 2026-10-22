"""Acceptance-only process egress denial; no host network/firewall changes."""
import ipaddress
import socket


def allowed(host):
    if host in (None, '', 'localhost'):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install():
    if getattr(socket, '_lzo_offline_guard', False):
        return
    original_resolve = socket.getaddrinfo
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex

    def resolve(host, *args, **kwargs):
        if not allowed(host):
            raise OSError('Acceptance process denies non-loopback DNS/egress')
        return original_resolve(host, *args, **kwargs)

    def connect(sock, address):
        if isinstance(address, tuple) and not allowed(address[0]):
            raise OSError('Acceptance process denies non-loopback egress')
        return original_connect(sock, address)

    def connect_ex(sock, address):
        if isinstance(address, tuple) and not allowed(address[0]):
            raise OSError('Acceptance process denies non-loopback egress')
        return original_connect_ex(sock, address)

    socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex = resolve, connect, connect_ex
    socket._lzo_offline_guard = True
