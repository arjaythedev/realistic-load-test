"""Local copy of 'Launchpad' (the Next.js app deployed on Vercel), reimplemented as a tiny server for staging tests.
Usage: python3 server.py <port>"""
import json, random, sys, time, hashlib, threading, uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
PRODUCTS = [{"slug": f"tool-{i}", "name": f"Tool {i}", "upvotes": random.randint(0, 900)} for i in range(1, 301)]
SESSIONS = {}; lock = threading.Lock()
def work(ms):
    end = time.perf_counter() + ms / 1000; h = b'x'
    while time.perf_counter() < end: h = hashlib.sha256(h).digest()
PAGE = """<!doctype html><html><head><title>Launchpad</title><script src="/_next/static/chunks/main.js"></script></head>
<body><main id=root>Loading…</main><script>
async function boot(){
  const me = await fetch('/api/session').then(r=>r.json());
  const list = await fetch('/api/products?sort=trending').then(r=>r.json());
  const counts = await fetch('/api/stats').then(r=>r.json());
  const root = document.getElementById('root');
  root.innerHTML = '<h1>Launchpad: '+counts.signups+' on the waitlist</h1>' + list.items.map(p =>
    '<div class=p><a href="#" data-slug="'+p.slug+'" class=open>'+p.name+'</a> <button data-slug="'+p.slug+'" class=up>▲ '+p.upvotes+'</button></div>').join('') +
    '<form id=wl><input name=email placeholder=email><button>Join waitlist</button></form>';
  root.querySelectorAll('.open').forEach(a=>a.onclick=async e=>{e.preventDefault();
    const [d,c]=await Promise.all([fetch('/api/products/'+a.dataset.slug),fetch('/api/products/'+a.dataset.slug+'/comments')]);});
  root.querySelectorAll('.up').forEach(b=>b.onclick=()=>fetch('/api/products/'+b.dataset.slug+'/upvote',{method:'POST'}));
  document.getElementById('wl').onsubmit=e=>{e.preventDefault();fetch('/api/waitlist',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({email:e.target.email.value})}).then(()=>alert('You are in!'))};
  setInterval(()=>fetch('/api/stats'),15000); // live waitlist counter
}
boot();
</script></body></html>"""
class H(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *a): pass
    def send(self, code, obj, ctype='application/json', cookie=None):
        b = obj.encode() if isinstance(obj, str) else json.dumps(obj).encode()
        self.send_response(code); self.send_header('Content-Type', ctype); self.send_header('Content-Length', str(len(b)))
        if cookie: self.send_header('Set-Cookie', f'sid={cookie}; Path=/; HttpOnly')
        self.end_headers(); self.wfile.write(b)
    def sid(self):
        c = self.headers.get('Cookie', '')
        return next((x.split('=', 1)[1] for x in c.split('; ') if x.startswith('sid=')), None)
    def do_GET(self):
        p = self.path.split('?')[0]; parts = p.strip('/').split('/')
        if p == '/': work(15); return self.send(200, PAGE, 'text/html')  # SSR
        if p.startswith('/_next/static/'): return self.send(200, '// js', 'application/javascript')
        if p == '/api/session':
            s = self.sid() or uuid.uuid4().hex
            with lock: SESSIONS[s] = time.time()
            return self.send(200, {"anon": True}, cookie=s)
        if p == '/api/products': work(12); return self.send(200, {"items": sorted(PRODUCTS, key=lambda x: -x['upvotes'])[:30]})
        if p == '/api/stats': work(20); return self.send(200, {"signups": 1000 + len(SESSIONS)})  # COUNT(*) on waitlist
        if len(parts) == 3 and parts[:2] == ['api', 'products']: work(3); return self.send(200, next((x for x in PRODUCTS if x['slug'] == parts[2]), {}))
        if len(parts) == 4 and parts[3] == 'comments': work(4); return self.send(200, {"comments": []})
        self.send(404, {"error": "not found"})
    def do_POST(self):
        n = int(self.headers.get('Content-Length') or 0); body = self.rfile.read(n); p = self.path.split('?')[0]; parts = p.strip('/').split('/')
        if not self.sid(): return self.send(401, {"error": "no session"})
        if p == '/api/waitlist': work(10); return self.send(201, {"ok": True})  # also sends a welcome email via Resend in prod
        if len(parts) == 4 and parts[3] == 'upvote': work(6); return self.send(200, {"ok": True})
        self.send(404, {})
ThreadingHTTPServer(('127.0.0.1', int(sys.argv[1])), H).serve_forever()
