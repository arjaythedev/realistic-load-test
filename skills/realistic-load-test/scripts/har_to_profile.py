#!/usr/bin/env python3
"""Summarize a browser session (HAR file) into the request sequence a k6 journey has to reproduce.

    npx playwright open --save-har=session.har https://staging.example.com     # click through the app, close the window
    python3 har_to_profile.py session.har --out browser-profile.json --md BROWSER.md

Reports, for requests to the app's own hosts (the page's host plus --host extras):
  - every request in order, grouped into user actions (a burst of requests < --burst s apart), with the pause
    before each action. When a person clicked through the app, those pauses are real think times.
  - route templates (/posts/123 -> /posts/:id), methods, status codes, request bodies of writes (truncated)
  - how the app authenticates (Authorization header scheme, cookie names; values are redacted)
  - third-party hosts the page calls (analytics, payments, auth providers): leave these out of the load test
Static assets (scripts, styles, images, fonts, media) are left out unless --include-static.
"""
import argparse, json, os, sys
from collections import Counter, OrderedDict
from datetime import datetime
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from analyze_logs import STATIC, seg_template, WRITE  # noqa: E402

STATIC_TYPES = {'image', 'font', 'stylesheet', 'script', 'media', 'manifest', 'texttrack', 'ping', 'other-static'}
STATIC_MIME = ('image/', 'font/', 'text/css', 'javascript', 'audio/', 'video/')


def ts(s):
    return datetime.fromisoformat(s.replace('Z', '+00:00')).timestamp()


def template(path):
    segs = [seg_template(s) for s in path.strip('/').split('/')] if path not in ('', '/') else []
    return '/' + '/'.join(segs)


def kind(e):
    t = e.get('_resourceType')
    if t:
        return t
    mime = (e.get('response', {}).get('content', {}).get('mimeType') or '').lower()
    if any(m in mime for m in STATIC_MIME):
        return 'other-static'
    if 'html' in mime:
        return 'document'
    return 'fetch'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('har', nargs='+')
    ap.add_argument('--out', default='browser-profile.json')
    ap.add_argument('--md')
    ap.add_argument('--host', action='append', default=[], help='extra first-party host (e.g. api.example.com); repeatable')
    ap.add_argument('--include-static', action='store_true')
    ap.add_argument('--burst', type=float, default=1.5, help='seconds between requests that still count as one action')
    a = ap.parse_args()

    entries = []
    for f in a.har:
        with open(f) as fh:
            entries += json.load(fh)['log']['entries']
    entries.sort(key=lambda e: ts(e['startedDateTime']))
    if not entries:
        sys.exit('HAR has no entries')

    first_doc = next((e for e in entries if kind(e) == 'document'), entries[0])
    main_host = urlsplit(first_doc['request']['url']).hostname
    own = {main_host, *a.host}
    base_domain = '.'.join(main_host.split('.')[-2:]) if main_host and main_host.count('.') >= 1 else main_host

    third, skipped_static = Counter(), Counter()
    reqs = []
    auth_schemes, cookie_names = Counter(), Counter()
    for e in entries:
        url = urlsplit(e['request']['url'])
        if url.scheme not in ('http', 'https'):
            continue
        host = url.hostname
        if host not in own:
            if base_domain and host and host.endswith('.' + base_domain):
                third[f'{host} (same domain; add --host {host} if it is your API)'] += 1
            else:
                third[host] += 1
            continue
        k = kind(e)
        if not a.include_static and (k in STATIC_TYPES or STATIC.search(url.path)):
            skipped_static[k] += 1
            continue
        method = e['request']['method'].upper()
        if method in ('OPTIONS', 'HEAD'):
            continue
        headers = {h['name'].lower(): h['value'] for h in e['request'].get('headers', [])}
        if 'authorization' in headers:
            auth_schemes[headers['authorization'].split(' ', 1)[0]] += 1
        for c in e['request'].get('cookies', []) or []:
            cookie_names[c['name']] += 1
        if not e['request'].get('cookies') and 'cookie' in headers:
            for part in headers['cookie'].split(';'):
                cookie_names[part.split('=', 1)[0].strip()] += 1
        body = (e['request'].get('postData') or {}).get('text')
        reqs.append({
            't': ts(e['startedDateTime']), 'method': method, 'host': host, 'route': template(url.path),
            'path': url.path + (f'?{url.query}' if url.query else ''), 'status': e['response'].get('status'),
            'type': k, 'time_ms': round(e.get('time') or 0, 1),
            'body': (body[:300] + ('…' if len(body) > 300 else '')) if body and method in WRITE else None,
        })
    if not reqs:
        sys.exit(f'no first-party requests to {sorted(own)}; pass --host for the API host')

    actions, cur = [], [reqs[0]]
    for prev, r in zip(reqs, reqs[1:]):
        if r['t'] - prev['t'] >= a.burst:
            actions.append(cur); cur = [r]
        else:
            cur.append(r)
    actions.append(cur)
    out_actions = []
    for i, act in enumerate(actions):
        pause = round(act[0]['t'] - actions[i - 1][-1]['t'], 1) if i else 0
        out_actions.append({'pause_before_s': pause, 'requests': [{k: v for k, v in r.items() if k != 't'} for r in act]})

    names = Counter(f"{r['method']} {r['route']}" for r in reqs)
    dur = reqs[-1]['t'] - reqs[0]['t']
    writes = sum(1 for r in reqs if r['method'] in WRITE)
    pauses = sorted(x['pause_before_s'] for x in out_actions[1:])
    profile = {
        'source': 'browser',
        'files': a.har,
        'first_party_hosts': sorted(own),
        'duration_s': round(dur, 1),
        'requests': len(reqs),
        'actions': len(actions),
        'per_user_rps': round(len(reqs) / dur, 4) if dur > 0 else None,
        'per_user_rps_note': 'only meaningful if a person clicked through at a natural pace',
        'write_share': round(writes / len(reqs), 4), 'read_share': round(1 - writes / len(reqs), 4),
        'think_time_s': {'min': pauses[0] if pauses else None, 'median': pauses[len(pauses) // 2] if pauses else None,
                         'max': pauses[-1] if pauses else None},
        'auth': {'authorization_schemes': dict(auth_schemes), 'cookie_names': [c for c, _ in cookie_names.most_common(15)]},
        'third_party_hosts': dict(third.most_common()),
        'skipped_static': dict(skipped_static),
        'endpoints': [{'name': n, 'count': c, 'share': round(c / len(reqs), 4), 'write': n.split(' ', 1)[0] in WRITE}
                      for n, c in names.most_common()],
        'sequence': out_actions,
    }
    with open(a.out, 'w') as fh:
        json.dump(profile, fh, indent=2)
    print(f'wrote {a.out}: {len(reqs)} first-party requests in {len(actions)} actions over {dur:.0f} s; '
          f'{len(third)} third-party hosts left out')
    if a.md:
        with open(a.md, 'w') as fh:
            fh.write(markdown(profile))
        print(f'wrote {a.md}')


def markdown(p):
    L = ['# Browser session', '',
         f"{p['requests']} first-party requests to {', '.join(p['first_party_hosts'])} in {p['actions']} actions over {p['duration_s']} s "
         f"(≈ {p['per_user_rps']} req/s if the pace was natural). Writes {100 * p['write_share']:.0f}%.", '',
         f"Auth: Authorization schemes {p['auth']['authorization_schemes'] or 'none'}; cookies {', '.join(p['auth']['cookie_names']) or 'none'}.", '',
         '## Third-party hosts (keep out of the load test)', '']
    L += [f'- {h}: {c}' for h, c in p['third_party_hosts'].items()] or ['- none']
    L += ['', '## Endpoints', '', '| endpoint | requests | write |', '|---|---:|:-:|']
    L += [f"| `{e['name']}` | {e['count']} | {'W' if e['write'] else ''} |" for e in p['endpoints']]
    L += ['', '## Sequence', '']
    for i, act in enumerate(p['sequence'], 1):
        L.append(f"**Action {i}** (after {act['pause_before_s']} s)")
        for r in act['requests']:
            L.append(f"- `{r['method']} {r['path'][:120]}` → {r['status']} ({r['type']}, {r['time_ms']} ms)" + (f"  body: `{r['body']}`" if r['body'] else ''))
        L.append('')
    return '\n'.join(L)


if __name__ == '__main__':
    main()
