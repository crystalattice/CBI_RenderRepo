"""Loopback-only review UI, independently reconnectable to a CLI worker."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from . import engine


def server(directory, port=8844):
    directory = Path(directory).resolve()
    token = secrets.token_urlsafe(32)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, code, body, media='application/json'):
            self.send_response(code)
            self.send_header('Content-Type', media)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def allowed(self, mutation=False):
            expected = f'127.0.0.1:{self.server.server_port}'
            return (self.headers.get('Host') == expected and
                    self.headers.get('Sec-Fetch-Site', 'same-origin') in {'same-origin', 'none'} and
                    (not mutation or (self.headers.get('X-RR-Review') == token and
                     self.headers.get('Origin') == 'http://' + expected)))

        def do_GET(self):
            if not self.allowed():
                return self.send(403, b'{}')
            path = urlsplit(self.path).path
            try:
                if path == '/':
                    body = (Path(__file__).parent / 'review.html').read_bytes()
                    return self.send(200, body, 'text/html; charset=utf-8')
                if path in {'/review.js', '/review.css'}:
                    body = (Path(__file__).parent / path[1:]).read_bytes()
                    return self.send(200, body, 'text/javascript' if path.endswith('.js') else 'text/css')
                if path == '/data':
                    rows = engine.results(directory)
                    for r in rows:
                        for o in r.get('outputs', []):
                            o['url'] = '/image/' + r['id'] + '/' + o['file']
                    return self.send(200, json.dumps({'token': token, 'name': engine.load(directory)['name'],
                                      'rows': rows, 'summary': engine.summarize(rows),
                                      'control': engine.read(directory / 'control.json'), 'execution_state': engine.execution_state(directory)}).encode())
                if path.startswith('/image/'):
                    _, _, job, name = path.split('/')
                    item = next(r for r in engine.results(directory) if r['id'] == job and r['state'] == 'completed')
                    output = next(o for o in item['outputs'] if o['file'] == name)
                    file = directory / 'jobs' / job / name
                    content = file.read_bytes()
                    if __import__('hashlib').sha256(content).hexdigest() != output['sha256']:
                        raise ValueError('Output hash mismatch')
                    return self.send(200, content, {'.png': 'image/png', '.jpg': 'image/jpeg', '.webp': 'image/webp'}[file.suffix])
                return self.send(404, b'{}')
            except (ValueError, OSError, KeyError, StopIteration):
                return self.send(409, b'{"error":"Record unavailable or corrupt"}')

        def do_POST(self):
            if not self.allowed(True):
                return self.send(403, b'{}')
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 8192:
                    raise ValueError('Bounded body required')
                body = json.loads(self.rfile.read(size))
                if self.path == '/review':
                    engine.review(directory, body['job'], body['rating'], body.get('notes', ''))
                elif self.path == '/control' and body['action'] in {'pause', 'stop'}:
                    engine.control(directory, body['action'])
                else:
                    return self.send(404, b'{}')
                self.send(200, b'{}')
            except (ValueError, OSError, KeyError, TypeError):
                self.send(400, b'{"error":"Invalid review or control"}')

    return ThreadingHTTPServer(('127.0.0.1', port), Handler)


def serve(directory, port):
    with server(directory, port) as http:
        print(f'Review: http://127.0.0.1:{http.server_port}/ (Ctrl-C closes review only)', flush=True)
        try:
            http.serve_forever()
        except KeyboardInterrupt:
            pass
