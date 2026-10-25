"""Staging copy of the 'Chirp' feed app (same code as production). Usage: python3 server.py <port>"""
import json, random, sys, time, hashlib, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
random.seed(7)
POSTS = {i: {"id": i, "author": f"user{random.randint(1,5000)}", "body": "x" * random.randint(40, 280), "likes": random.randint(0, 500)} for i in range(1, 20001)}
TOKENS = set(json.load(open(__file__.replace('server.py', 'test-tokens.json'))))
lock = threading.Lock()
def work(ms):  # simulated DB/CPU cost
    end = time.perf_counter() + ms / 1000
    h = b'x'
    while time.perf_counter() < end: h = hashlib.sha256(h).digest()
class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def send(self, code, obj):
        b = json.dumps(obj).encode(); self.send_response(code)
        self.send_header('Content-Type', 'application/json'); self.send_header('Content-Length', str(len(b))); self.end_headers(); self.wfile.write(b)
    def authed(self):
        a = self.headers.get('Authorization', '')
        return a.startswith('Bearer ') and a[7:] in TOKENS
    def do_GET(self):
        p, _, q = self.path.partition('?'); parts = p.strip('/').split('/')
        if p == '/': work(1); b = b'<html><body><div id=app></div><script src="/static/app.js"></script></body></html>'; self.send_response(200); self.send_header('Content-Type','text/html'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if p == '/health': return self.send(200, {"ok": True})
        if p == '/api/feed':
            work(4); ids = sorted(POSTS, key=lambda i: -POSTS[i]['likes'])[:20] if random.random() < 0.01 else random.sample(range(1, 20001), 20)
            return self.send(200, {"posts": [POSTS[i] for i in ids]})
        if p == '/api/search': work(25); return self.send(200, {"results": [POSTS[i] for i in random.sample(range(1, 20001), 10)]})
        if len(parts) == 3 and parts[:2] == ['api', 'posts'] and parts[2].isdigit():
            work(2); i = int(parts[2]); return self.send(200, POSTS[i]) if i in POSTS else self.send(404, {})
        if len(parts) == 4 and parts[:2] == ['api', 'posts'] and parts[3] == 'comments': work(3); return self.send(200, {"comments": []})
        if p.startswith('/static/'): return self.send(200, {})
        self.send(404, {"error": "not found"})
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0); body = self.rfile.read(n)
        if not self.authed(): return self.send(401, {"error": "auth"})
        p = self.path.split('?')[0]; parts = p.strip('/').split('/')
        if len(parts) == 4 and parts[:2] == ['api', 'posts'] and parts[3] == 'like':
            work(5)
            with lock: POSTS.get(int(parts[2]), {"likes": 0})['likes'] += 1
            return self.send(200, {"ok": True})
        if p == '/api/posts':
            work(8)
            with lock: i = max(POSTS) + 1; POSTS[i] = {"id": i, "author": "test", "body": json.loads(body or b'{}').get('body', ''), "likes": 0}
            return self.send(201, POSTS[i])
        self.send(404, {})
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
