"""Local HTTP API for RFID → metadata lookup.
Run: python api_service.py --host 127.0.0.1 --port 8077

GET /health -> {status: ok}
GET /mouse?rfid=TAG -> denormalized JSON or {error:not_found}
"""
from __future__ import annotations
import argparse, pathlib, json
from typing import Optional
from http.server import BaseHTTPRequestHandler, HTTPServer
import urllib.parse as up
import db

_conn = None

def get_conn():
    global _conn
    if _conn is None:
        db.init()
        _conn = db.connect()
    return _conn

class Handler(BaseHTTPRequestHandler):
    def _set(self, code=200, ctype='application/json'):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.end_headers()

    def do_GET(self):  # noqa
        parsed = up.urlparse(self.path)
        if parsed.path == '/health':
            self._set(); self.wfile.write(b'{"status":"ok"}')
            return
        if parsed.path == '/mouse':
            qs = up.parse_qs(parsed.query)
            rfid = qs.get('rfid', [None])[0]
            if not rfid:
                self._set(400); self.wfile.write(b'{"error":"missing_rfid"}')
                return
            rec = db.get_mouse(get_conn(), rfid)
            if not rec:
                self._set(404); self.wfile.write(json.dumps({'error':'not_found','rfid':rfid}).encode())
                return
            self._set(200); self.wfile.write(json.dumps(rec).encode()); return
        self._set(404); self.wfile.write(b'{"error":"unknown_endpoint"}')


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('--host', default='127.0.0.1')
    ap.add_argument('--port', type=int, default=8077)
    args = ap.parse_args(argv)
    srv = HTTPServer((args.host, args.port), Handler)
    print(f'RFID metadata service listening on http://{args.host}:{args.port}')
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print('\nShutting down.')

if __name__ == '__main__':
    main()
