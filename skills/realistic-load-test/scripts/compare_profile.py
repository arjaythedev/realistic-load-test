#!/usr/bin/env python3
"""Check that the k6 virtual user behaves like the real one (phase 3, calibration).

    python3 compare_profile.py profile.json results/calibrate-30vu-<time>.json [--md PROFILE.md]

Compares the per-user request rate and the endpoint shares measured by hold.js with the profile from
analyze_logs.py (or a hand-written profile.json with the same keys: per_user_rps, endpoints[{name, share}]).
Endpoint names must match the k6 `name` tags (e.g. "GET /posts/:id"). Profile endpoints the journey never calls
are listed, so you can see which part of real traffic the test leaves out.
Exits 1 when the rate is off by more than --rate-tol or any endpoint share by more than --share-tol points.
"""
import argparse, json, sys


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('profile')
    ap.add_argument('result')
    ap.add_argument('--md', help='append the comparison table to this Markdown file')
    ap.add_argument('--rate-tol', type=float, default=0.15, help='allowed relative error of per-user req/s (default 0.15)')
    ap.add_argument('--share-tol', type=float, default=3.0, help='allowed difference in endpoint share, percentage points (default 3)')
    a = ap.parse_args()

    prof = json.load(open(a.profile))
    res = json.load(open(a.result))['result']
    want_rate, got_rate = prof['per_user_rps'], res['per_user_rps']
    rate_err = (got_rate - want_rate) / want_rate
    total = res['requests'] or 1
    got = {n: s['count'] / total for n, s in res['endpoints'].items()}
    want = {e['name']: e['share'] for e in prof['endpoints']}
    # the profile's shares include endpoints the journey leaves out; renormalize over the ones it covers
    covered = sum(v for n, v in want.items() if n in got)
    want_cov = {n: v / covered for n, v in want.items() if n in got} if covered else {}

    rows, bad = [], abs(rate_err) > a.rate_tol
    for n in sorted(set(want_cov) | set(got), key=lambda n: -(want_cov.get(n, 0) + got.get(n, 0))):
        w, g = 100 * want_cov.get(n, 0), 100 * got.get(n, 0)
        off = abs(g - w) > a.share_tol
        bad |= off
        rows.append(f'| `{n}` | {w:.1f}% | {g:.1f}% | {g - w:+.1f} | {"⚠" if off else "ok"} |')
    missing = sorted(((n, v) for n, v in want.items() if n not in got), key=lambda kv: -kv[1])

    L = ['', '## Calibration', '',
         f'Per-user rate: profile **{want_rate:.4f} req/s**, k6 **{got_rate:.4f} req/s** ({100 * rate_err:+.1f}%) '
         f'{"⚠ outside" if abs(rate_err) > a.rate_tol else "within"} ±{100 * a.rate_tol:.0f}%.', '',
         f'Endpoint shares, renormalized over the {len(want_cov)} profiled endpoints the journey calls '
         f'({100 * covered:.1f}% of real traffic):', '',
         '| endpoint | profile | k6 | diff (pts) | |', '|---|---:|---:|---:|---|', *rows]
    if missing:
        L += ['', f'Not in the journey ({100 * (1 - covered):.1f}% of real traffic): ' +
              ', '.join(f'`{n}` {100 * v:.1f}%' for n, v in missing[:12]) + (' …' if len(missing) > 12 else '')]
    L.append('')
    text = '\n'.join(L)
    print(text)
    if a.md:
        with open(a.md, 'a') as fh:
            fh.write(text)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    main()
