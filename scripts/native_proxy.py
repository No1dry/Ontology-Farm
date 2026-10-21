"""Supplemental loopback HTTPS reverse proxy; Compose uses nginx instead."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import ssl
import sys
import os
import mimetypes
from urllib.parse import urlsplit, unquote
import httpx

ROOT = Path(__file__).resolve().parents[1]
HOP_HEADERS = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
               "te", "trailers", "transfer-encoding", "upgrade"}


class Proxy(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def relay(self):
        path = urlsplit(self.path).path
        if os.environ.get("LZO_WEB_DIST") and not path.startswith(("/identity/", "/api/", "/health/")):
            root = Path(os.environ["LZO_WEB_DIST"]).resolve()
            asset = (root / unquote(path).lstrip("/")).resolve()
            if not asset.is_relative_to(root):
                self.send_error(404); return
            if not asset.is_file():
                if path.startswith("/assets/"):
                    self.send_error(404); return
                asset = root / "index.html"
            if not asset.is_file():
                self.send_error(503, "Workbench not built"); return
            if self.command not in {"GET", "HEAD"}:
                self.send_error(405); return
            content = asset.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(asset)[0] or "application/octet-stream")
            self.send_header("Cache-Control", "no-store" if asset.name == "index.html" else "public, max-age=3600")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            if self.command != "HEAD": self.wfile.write(content)
            return
        target = "http://127.0.0.1:" + (os.environ.get("LZO_PROXY_IDENTITY_PORT", "18080") if path.startswith("/identity/") else os.environ.get("LZO_PROXY_API_PORT", "18000"))
        headers = {k: v for k, v in self.headers.items() if k.lower() not in HOP_HEADERS}
        headers.update({"X-Forwarded-Proto": "https", "X-Forwarded-Host": self.headers.get("Host", "localhost:18443"),
                        "X-Forwarded-Port": str(self.server.server_port), "X-Forwarded-For": "127.0.0.1"})
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        try:
            with httpx.Client(timeout=60, follow_redirects=False) as client:
                response = client.request(self.command, target + self.path, headers=headers, content=body)
            self.send_response(response.status_code)
            for key, value in response.headers.multi_items():
                if key.lower() not in HOP_HEADERS | {"content-length", "content-encoding"}:
                    self.send_header(key, value)
            self.send_header("Content-Length", str(len(response.content)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.content)
        except httpx.HTTPError:
            self.send_error(502, "Local upstream unavailable")

    do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = do_HEAD = relay

    def log_message(self, format, *args):
        # Paths can contain authorization codes; do not log request URLs.
        pass


if __name__ == "__main__":
    directory = Path(sys.argv[1])
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("LZO_PROXY_PORT", "18443"))), Proxy)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(directory / "localhost.crt", directory / "localhost.key")
    server.socket = context.wrap_socket(server.socket, server_side=True)
    server.serve_forever()
