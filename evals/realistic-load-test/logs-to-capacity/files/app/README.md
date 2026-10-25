# Chirp (staging)
Same code as production (chirp.app, a single $24 VPS behind Nginx). Run locally as staging: `python3 server.py <port>`.
API: GET / (SSR shell), GET /api/feed, GET /api/posts/:id, GET /api/posts/:id/comments, GET /api/search?q=,
POST /api/posts/:id/like, POST /api/posts (Bearer token). test-tokens.json = 2,000 staging test accounts' tokens.
