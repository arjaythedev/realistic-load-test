#!/usr/bin/env python3
"""Turn HTTP request logs into a per-user traffic profile for a realistic load test.

    python3 analyze_logs.py access.log [more.log ...] --out profile.json --md PROFILE.md

Reads (auto-detected per line): Nginx/Apache common or combined log format (an optional trailing
$request_time is used as latency), JSON lines (Vercel log drains and `vercel logs --json` included), and
CSV with a header row. .gz files are fine.

What it reports:
  per_user_rps     requests per second one active user generates (total requests / total user-active time)
  endpoints        route templates (/posts/123 -> /posts/:id), share, read or write, status codes, latency
  actions          requests per user action (a burst of requests < --burst seconds apart) and think time
                   between actions
  concurrency      mean and peak concurrently active users; traffic per minute
Static assets, bots/monitors and health checks are counted and dropped (see --include-static).

A user is the --user-field value when the logs have one, otherwise client IP + user agent. A session ends after
--idle minutes without a request. A user counts as active from their first request until their last request plus
one median think time (they are still reading the last page).
"""
import argparse, csv, gzip, io, json, math, re, statistics, sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

CANDIDATES = {
    'time': ['timestamp', 'time', 'ts', '@timestamp', 'date', 'datetime', 'proxy.timestamp', 'requestTime', 'start_time'],
    'method': ['method', 'request_method', 'requestMethod', 'http.method', 'proxy.method', 'request.method', 'httpRequest.requestMethod'],
    'path': ['path', 'request_path', 'requestPath', 'uri', 'request_uri', 'url', 'proxy.path', 'request.path', 'request.url', 'http.url', 'httpRequest.requestUrl', 'requestUrl'],
    'status': ['status', 'status_code', 'statusCode', 'response_status', 'proxy.statusCode', 'response.status', 'http.status_code', 'httpRequest.status'],
    'ip': ['ip', 'client_ip', 'clientIp', 'remote_addr', 'remoteAddr', 'proxy.clientIp', 'x_forwarded_for', 'httpRequest.remoteIp', 'network.client.ip'],
    'ua': ['user_agent', 'userAgent', 'ua', 'http_user_agent', 'proxy.userAgent', 'http.useragent', 'httpRequest.userAgent'],
    'user': ['user_id', 'userId', 'uid', 'session_id', 'sessionId', 'usr.id', 'user.id'],
    'duration': ['duration_ms', 'durationMs', 'duration', 'request_time', 'response_time', 'responseTime', 'latency', 'latency_ms', 'elapsed', 'httpRequest.latency'],
    'host': ['host', 'proxy.host', 'http.host', 'request.host'],
    'cache': ['proxy.vercelCache', 'cache', 'cache_status', 'cacheStatus', 'upstream_cache_status', 'cf.cacheStatus'],
    'id': ['requestId', 'request_id', 'proxy.requestId', 'id'],
}

COMBINED = re.compile(
    r'^(?P<ip>\S+) \S+ (?P<user>\S+) \[(?P<time>[^\]]+)\] "(?P<method>[A-Z]+) (?P<path>\S+)[^"]*" (?P<status>\d{3}) \S+'
    r'(?: "(?P<ref>[^"]*)" "(?P<ua>[^"]*)")?(?P<rest>.*)$')

STATIC = re.compile(
    r'(\.(js|mjs|css|map|png|jpe?g|gif|svg|ico|webp|avif|bmp|woff2?|ttf|otf|eot|mp4|webm|mp3|wav|ogg|txt|xml|webmanifest|json\.br)$)'
    r'|^/(_next/(static|image|data/[^/]+/_buildManifest)|static/|assets/|build/|public/|images?/|img/|fonts?/|favicon|apple-touch-icon|robots\.txt|sitemap|_vercel/|cdn-cgi/|\.well-known/)',
    re.I)
HEALTH = re.compile(r'^/(api/)?(health|healthz|healthcheck|ping|ready|readyz|live|livez|status)/?$', re.I)
BOT = re.compile(r'bot|crawl|spider|slurp|monitor|uptime|pingdom|statuscake|checkly|headless|lighthouse|curl/|wget|'
                 r'python-requests|httpx|aiohttp|go-http-client|okhttp|java/|k6/|loadtest|facebookexternalhit|'
                 r'preview|vercel-screenshot|scrapy|axios/|node-fetch|undici|postman', re.I)
WRITE = {'POST', 'PUT', 'PATCH', 'DELETE'}

UUID = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)
HEX = re.compile(r'^[0-9a-f]{16,}$', re.I)
NUM = re.compile(r'^\d+$')
MIXED_ID = re.compile(r'^(?=.*\d)(?=.*[A-Za-z])[A-Za-z0-9_-]{8,}$')
EMAIL = re.compile(r'^[^@/]+@[^@/]+\.[a-z]{2,}$', re.I)


def dig(obj, dotted):
    for part in dotted.split('.'):
        if isinstance(obj, dict) and part in obj:
            obj = obj[part]
        else:
            return None
    return obj


def pick(rec, field, overrides):
    keys = [overrides[field]] if field in overrides else CANDIDATES[field]
    for k in keys:
        v = dig(rec, k) if '.' in k else rec.get(k)
        if v not in (None, '', '-'):
            if isinstance(v, list):
                v = ' '.join(map(str, v))
            return v
    return None


def parse_time(v):
    if v is None:
        return None
    if isinstance(v, (int, float)) or (isinstance(v, str) and re.fullmatch(r'\d+(\.\d+)?', v)):
        x = float(v)
        if x > 1e17: x /= 1e9      # ns
        elif x > 1e14: x /= 1e6    # us
        elif x > 1e11: x /= 1e3    # ms
        return x
    s = str(v).strip()
    for fmt in ('%d/%b/%Y:%H:%M:%S %z',):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    try:
        d = datetime.fromisoformat(s.replace('Z', '+00:00'))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def parse_duration_ms(v, field_name):
    if v is None:
        return None
    try:
        if isinstance(v, str) and v.endswith('s') and not v.endswith('ms'):  # GCP "0.123s"
            return float(v[:-1]) * 1000
        x = float(str(v).rstrip('ms'))
    except ValueError:
        return None
    # request_time / response_time in Nginx are seconds; *_ms are ms; bare numbers < 60 with a decimal point are seconds
    if field_name in ('request_time',) or (isinstance(v, str) and '.' in v and x < 60 and 'ms' not in field_name.lower()):
        return x * 1000
    return x


def open_any(path):
    if path == '-':
        return sys.stdin
    if path.endswith('.gz'):
        return io.TextIOWrapper(gzip.open(path), errors='replace')
    return open(path, errors='replace')


def records(paths, overrides):
    """Yields normalized dicts: t, method, path, status, ip, ua, user, dur_ms, host, cache, id."""
    for p in paths:
        with open_any(p) as fh:
            first = fh.readline()
            if not first:
                continue
            csv_mode = not first.lstrip().startswith('{') and not COMBINED.match(first) and ',' in first
            if csv_mode:
                reader = csv.DictReader(io.StringIO(first + fh.read()))
                for row in reader:
                    yield from norm(row, overrides)
                continue
            for line in _chain(first, fh):
                line = line.strip()
                if not line:
                    continue
                if line.startswith('{'):
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(rec, dict) and isinstance(rec.get('message'), str) and rec['message'].startswith('{'):
                        try:  # some drains wrap the request JSON in "message"
                            rec = {**rec, **json.loads(rec['message'])}
                        except json.JSONDecodeError:
                            pass
                    yield from norm(rec, overrides)
                else:
                    m = COMBINED.match(line)
                    if not m:
                        continue
                    g = m.groupdict()
                    rest = (g.get('rest') or '').split()
                    dur = None
                    if rest:
                        try:
                            dur = float(rest[-1]) * 1000  # $request_time appended (seconds)
                        except ValueError:
                            pass
                    yield {'t': parse_time(g['time']), 'method': g['method'], 'path': g['path'], 'status': int(g['status']),
                           'ip': g['ip'], 'ua': g.get('ua') or '', 'user': None if g['user'] == '-' else g['user'],
                           'dur_ms': dur, 'host': None, 'cache': None, 'id': None}


def _chain(first, fh):
    yield first
    yield from fh


def norm(rec, overrides):
    path = pick(rec, 'path', overrides)
    t = parse_time(pick(rec, 'time', overrides))
    if path is None or t is None:
        return
    path = str(path)
    if path.startswith('http://') or path.startswith('https://'):
        path = '/' + path.split('/', 3)[3] if path.count('/') >= 3 else '/'
    method = str(pick(rec, 'method', overrides) or 'GET').upper()
    status = pick(rec, 'status', overrides)
    try:
        status = int(status) if status is not None else None
    except ValueError:
        status = None
    dkey = overrides.get('duration') or next((k for k in CANDIDATES['duration'] if (dig(rec, k) if '.' in k else rec.get(k)) not in (None, '')), None)
    dur = parse_duration_ms(pick(rec, 'duration', overrides), dkey or '') if dkey else None
    yield {'t': t, 'method': method, 'path': path, 'status': status, 'ip': pick(rec, 'ip', overrides),
           'ua': str(pick(rec, 'ua', overrides) or ''), 'user': pick(rec, 'user', overrides), 'dur_ms': dur,
           'host': pick(rec, 'host', overrides), 'cache': pick(rec, 'cache', overrides), 'id': pick(rec, 'id', overrides)}


def seg_template(s):
    if NUM.match(s): return ':id'
    if UUID.match(s): return ':uuid'
    if HEX.match(s): return ':hash'
    if EMAIL.match(s): return ':email'
    if MIXED_ID.match(s): return ':id'
    return s


def collapse(paths, threshold):
    """paths: Counter of segment tuples. Positions with more than `threshold` distinct literal values under the same
    prefix become :param (slugs, usernames). Returns {original tuple: template tuple}."""
    mapping = {p: list(p) for p in paths}

    def walk(group, depth):
        by_seg = defaultdict(list)
        for p in group:
            if len(mapping[p]) > depth:
                by_seg[mapping[p][depth]].append(p)
        literals = [s for s in by_seg if not s.startswith(':')]
        if len(literals) > threshold:
            for s in literals:
                for p in by_seg[s]:
                    mapping[p][depth] = ':param'
            by_seg = defaultdict(list)
            for p in group:
                if len(mapping[p]) > depth:
                    by_seg[mapping[p][depth]].append(p)
        for sub in by_seg.values():
            walk(sub, depth + 1)

    walk(list(paths), 0)
    return {p: tuple(v) for p, v in mapping.items()}


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * q
    lo, hi = math.floor(k), math.ceil(k)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def r(x, d=3):
    return None if x is None else round(x, d)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('logs', nargs='+', help='log files (.gz ok, - for stdin)')
    ap.add_argument('--out', default='profile.json')
    ap.add_argument('--md', help='also write a Markdown summary here')
    ap.add_argument('--host', help='keep only requests for this host (when the logs mix several sites)')
    ap.add_argument('--include-static', action='store_true', help='keep static assets (e.g. no CDN in front)')
    ap.add_argument('--keep-bots', action='store_true')
    ap.add_argument('--exclude-cache-hits', action='store_true', help='drop requests the CDN served from cache (needs a cache field)')
    ap.add_argument('--idle', type=float, default=30, help='minutes without a request that end a session (default 30)')
    ap.add_argument('--burst', type=float, default=2.0, help='requests closer than this many seconds are one user action (default 2)')
    ap.add_argument('--param-threshold', type=int, default=30, help='distinct values at a path position before it becomes :param')
    ap.add_argument('--top', type=int, default=40, help='endpoints listed in the Markdown')
    ap.add_argument('--field', action='append', default=[], metavar='NAME=PATH',
                    help=f'override a field lookup, e.g. --field path=req.url --field user=ctx.userId. NAMEs: {", ".join(CANDIDATES)}')
    a = ap.parse_args()
    overrides = dict(f.split('=', 1) for f in a.field)

    dropped = Counter()
    kept, seen_ids = [], set()
    for rec in records(a.logs, overrides):
        if rec['id'] is not None:
            if rec['id'] in seen_ids:
                dropped['duplicate request id'] += 1
                continue
            seen_ids.add(rec['id'])
        if a.host and rec['host'] and str(rec['host']).lower() != a.host.lower():
            dropped['other host'] += 1
            continue
        path_only = rec['path'].split('?', 1)[0].split('#', 1)[0] or '/'
        if not a.include_static and STATIC.search(path_only):
            dropped['static asset'] += 1
            continue
        if HEALTH.match(path_only):
            dropped['health check'] += 1
            continue
        if not a.keep_bots and (not rec['ua'] or BOT.search(rec['ua'])):
            dropped['bot / monitor / no user agent'] += 1
            continue
        if rec['method'] in ('OPTIONS', 'HEAD'):
            dropped['OPTIONS/HEAD'] += 1
            continue
        if a.exclude_cache_hits and rec['cache'] and str(rec['cache']).upper() in ('HIT', 'STALE', 'PRERENDER'):
            dropped['CDN cache hit'] += 1
            continue
        rec['path_only'] = path_only
        rec['query_keys'] = sorted({kv.split('=', 1)[0] for kv in rec['path'].split('?', 1)[1].split('&')}) if '?' in rec['path'] else []
        kept.append(rec)

    if not kept:
        sys.exit(f'no usable requests (dropped: {dict(dropped)}). Check the format, or pass --field overrides.')
    kept.sort(key=lambda x: x['t'])

    # route templates
    raw = Counter(tuple(seg_template(s) for s in rec['path_only'].strip('/').split('/')) if rec['path_only'] != '/' else () for rec in kept)
    mapping = collapse(raw, a.param_threshold)
    for rec in kept:
        segs = tuple(seg_template(s) for s in rec['path_only'].strip('/').split('/')) if rec['path_only'] != '/' else ()
        rec['route'] = '/' + '/'.join(mapping[segs])
        rec['name'] = f"{rec['method']} {rec['route']}"

    # sessions -> actions and think times
    idle_s = a.idle * 60
    by_user = defaultdict(list)
    for rec in kept:
        key = rec['user'] if rec['user'] else f"{rec['ip']}|{rec['ua']}"
        by_user[key].append(rec['t'])
    sessions = []  # (start, last, n_requests, [action start times], [requests per action])
    for ts in by_user.values():
        cur = [ts[0]]
        for t in ts[1:]:
            if t - cur[-1] > idle_s:
                sessions.append(cur); cur = [t]
            else:
                cur.append(t)
        sessions.append(cur)
    thinks, per_action = [], []
    for s in sessions:
        starts, n = [s[0]], 1
        for prev, t in zip(s, s[1:]):
            if t - prev >= a.burst:
                thinks.append(t - prev)
                per_action.append(n); n = 1; starts.append(t)
            else:
                n += 1
        per_action.append(n)
    tail = statistics.median(thinks) if thinks else 10.0
    spans = [(s[0], s[-1] + tail) for s in sessions]
    active_seconds = sum(e - b for b, e in spans)
    t0, t1 = kept[0]['t'], kept[-1]['t']
    window = max(t1 - t0, 1.0)

    # concurrency over time (sweep)
    events = sorted([(b, 1) for b, _ in spans] + [(e, -1) for _, e in spans])
    cur = peak = 0; peak_at = t0
    for t, d in events:
        cur += d
        if cur > peak:
            peak, peak_at = cur, t
    per_minute = Counter(int(rec['t'] // 60) for rec in kept)
    pm_peak_min, pm_peak = max(per_minute.items(), key=lambda kv: kv[1])
    per_second = Counter(int(rec['t']) for rec in kept)

    # endpoints
    eps = defaultdict(lambda: {'count': 0, 'status': Counter(), 'dur': [], 'cache_hits': 0, 'cache_known': 0, 'query_keys': Counter()})
    for rec in kept:
        e = eps[rec['name']]
        e['count'] += 1
        if rec['status'] is not None:
            e['status'][f"{rec['status'] // 100}xx"] += 1
        if rec['dur_ms'] is not None:
            e['dur'].append(rec['dur_ms'])
        if rec['cache']:
            e['cache_known'] += 1
            e['cache_hits'] += str(rec['cache']).upper() in ('HIT', 'STALE', 'PRERENDER')
        for k in rec['query_keys']:
            e['query_keys'][k] += 1
    total = len(kept)
    endpoints = []
    for name, e in sorted(eps.items(), key=lambda kv: -kv[1]['count']):
        method, route = name.split(' ', 1)
        endpoints.append({
            'name': name, 'method': method, 'route': route, 'count': e['count'], 'share': r(e['count'] / total, 4),
            'write': method in WRITE, 'status': dict(e['status']),
            'p50_ms': r(pct(e['dur'], .5), 1), 'p95_ms': r(pct(e['dur'], .95), 1),
            'cache_hit_share': r(e['cache_hits'] / e['cache_known'], 3) if e['cache_known'] else None,
            'query_keys': [k for k, _ in e['query_keys'].most_common(6)],
        })
    writes = sum(e['count'] for e in endpoints if e['write'])
    errors5 = sum(e['status'].get('5xx', 0) for e in endpoints)

    hourly = Counter(datetime.fromtimestamp(rec['t'], timezone.utc).strftime('%Y-%m-%d %H:00') for rec in kept)
    iso = lambda t: datetime.fromtimestamp(t, timezone.utc).isoformat(timespec='seconds')
    sess_n = [len(s) for s in sessions]
    sess_d = [e - b for b, e in spans]
    profile = {
        'source': 'logs',
        'files': a.logs,
        'window': {'start': iso(t0), 'end': iso(t1), 'hours': r(window / 3600, 2)},
        'totals': {'kept': total, 'dropped': dict(dropped)},
        'per_user_rps': r(total / active_seconds, 4),
        'per_user_rps_note': 'kept requests / user-active seconds; one VU should generate about this many req/s',
        'read_share': r(1 - writes / total, 4), 'write_share': r(writes / total, 4),
        'server_error_share': r(errors5 / total, 4),
        'sessions': {'count': len(sessions), 'users': len(by_user), 'median_requests': statistics.median(sess_n),
                     'median_duration_s': r(statistics.median(sess_d), 1), 'mean_duration_s': r(statistics.mean(sess_d), 1)},
        'actions': {'requests_per_action_mean': r(statistics.mean(per_action), 2),
                    'requests_per_action_median': statistics.median(per_action),
                    'think_time_s': {q: r(pct(thinks, v), 1) for q, v in (('p10', .1), ('p25', .25), ('p50', .5), ('p75', .75), ('p90', .9))}},
        'concurrency': {'mean': r(active_seconds / window, 1), 'peak': peak, 'peak_at': iso(peak_at)},
        'traffic': {'mean_rps': r(total / window, 2), 'peak_minute_rps': r(pm_peak / 60, 2), 'peak_minute_at': iso(pm_peak_min * 60),
                    'peak_second_rps': max(per_second.values())},
        'hourly_requests': dict(sorted(hourly.items())),
        'endpoints': endpoints,
    }
    with open(a.out, 'w') as fh:
        json.dump(profile, fh, indent=2)
    print(f'wrote {a.out}: {total} requests, {len(sessions)} sessions, {profile["per_user_rps"]} req/s per active user, '
          f'{100 * profile["write_share"]:.1f}% writes, peak {peak} concurrent users')
    if a.md:
        with open(a.md, 'w') as fh:
            fh.write(markdown(profile, a.top))
        print(f'wrote {a.md}')


def markdown(p, top):
    t = p['actions']['think_time_s']
    L = [
        '# Traffic profile (from logs)', '',
        f"Window: {p['window']['start']} → {p['window']['end']} ({p['window']['hours']} h), {p['totals']['kept']:,} requests kept.",
        f"Dropped: {', '.join(f'{k} {v:,}' for k, v in p['totals']['dropped'].items()) or 'nothing'}.", '',
        '## One active user', '',
        f"- **{p['per_user_rps']} requests/second** per active user (one request every {1 / p['per_user_rps']:.1f} s on average)",
        f"- {p['actions']['requests_per_action_mean']} requests per action (burst); think time between actions p25 {t['p25']} s, "
        f"median {t['p50']} s, p75 {t['p75']} s, p90 {t['p90']} s",
        f"- reads {100 * p['read_share']:.1f}% / writes {100 * p['write_share']:.1f}%; 5xx in production {100 * p['server_error_share']:.2f}%",
        f"- sessions: {p['sessions']['count']:,} from {p['sessions']['users']:,} users; median {p['sessions']['median_requests']} requests "
        f"over {p['sessions']['median_duration_s']} s", '',
        '## Load seen in production', '',
        f"- concurrent active users: mean {p['concurrency']['mean']}, peak {p['concurrency']['peak']} at {p['concurrency']['peak_at']}",
        f"- requests/second: mean {p['traffic']['mean_rps']}, busiest minute {p['traffic']['peak_minute_rps']} "
        f"({p['traffic']['peak_minute_at']}), busiest second {p['traffic']['peak_second_rps']}", '',
        '## Endpoint mix', '',
        '| endpoint | share | requests | write | p50 ms | p95 ms | CDN hit | status |',
        '|---|---:|---:|:-:|---:|---:|---:|---|',
    ]
    for e in p['endpoints'][:top]:
        hit = '' if e['cache_hit_share'] is None else f"{100 * e['cache_hit_share']:.0f}%"
        st = ' '.join(f'{k}:{v}' for k, v in sorted(e['status'].items()))
        L.append(f"| `{e['name']}` | {100 * e['share']:.2f}% | {e['count']:,} | {'W' if e['write'] else ''} | "
                 f"{e['p50_ms'] if e['p50_ms'] is not None else ''} | {e['p95_ms'] if e['p95_ms'] is not None else ''} | {hit} | {st} |")
    rest = p['endpoints'][top:]
    if rest:
        L.append(f"| *{len(rest)} more* | {100 * sum(e['share'] for e in rest):.2f}% | {sum(e['count'] for e in rest):,} | | | | | |")
    L += ['', 'Check before trusting this: are the top endpoints what you expect? Are many users behind one IP '
          '(offices, mobile carriers), which merges them and lowers the user count? Did anything uncached land in '
          'the static filter, or anything static survive it?', '']
    return '\n'.join(L)


if __name__ == '__main__':
    main()
