"""A stand-in for Zenodo's deposit API, to test zenodo_upload.sh end to end on this machine: it answers the three calls
the script makes, needs the bearer header, hashes what it is sent, and says whether the body came with a length or in
chunks. One draft, kept in memory; files are hashed and dropped, never written.

python scripts/release/fake_zenodo.py PORT
"""
import hashlib
import json
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

PORT = int(sys.argv[1])
FILES = []


class H(BaseHTTPRequestHandler):
    def _send(self, code, obj):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authed(self):
        if not (self.headers.get("Authorization") or "").startswith("Bearer "):
            self._send(401, {"message": "no token"})
            return False
        return True

    def do_POST(self):
        if self._authed() and self.path == "/api/deposit/depositions":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._send(201, {"id": 1})

    def do_GET(self):
        if self._authed() and self.path == "/api/deposit/depositions/1":
            self._send(200, {"id": 1, "links": {"bucket": f"http://127.0.0.1:{PORT}/api/files/b1"},
                             "files": [{"filename": f} for f in FILES]})

    def do_PUT(self):
        if not self._authed():
            return
        name = self.path.rsplit("/", 1)[-1]
        chunked = "chunked" in (self.headers.get("Transfer-Encoding") or "")
        n = int(self.headers.get("Content-Length") or -1)
        h, got = hashlib.md5(), 0
        while got < n:
            b = self.rfile.read(min(1 << 20, n - got))
            if not b:
                break
            h.update(b)
            got += len(b)
        print(f"PUT {name}: length header {n}, chunked {chunked}, bytes read {got}", flush=True)
        FILES.append(name)
        self._send(201, {"key": name, "checksum": "md5:" + h.hexdigest(), "size": got})

    def log_message(self, *a):
        pass


HTTPServer(("127.0.0.1", PORT), H).serve_forever()
