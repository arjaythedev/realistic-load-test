#!/usr/bin/env bash
# Confirmation holds: each level N times (default 3), cooldown between runs, verdict = median of the runs.
#   BASE_URL=https://staging.example.com ./run-holds.sh 1500 2000 2500
# Env: RUNS (3), COOLDOWN (60 s), DURATION (5m), RAMP_UP (60s), RESULTS_DIR (results), plus anything hold.js reads
# (P95_MS, P99_MS, MAX_ERROR_RATE, ACCOUNTS_FILE, ...), which k6 picks up from the environment.
# Prints one line per run and a median line per level; writes results/holds-summary.txt.
# Single load generator only. With several, start hold.js on all of them at once (see load-generators.md).
set -uo pipefail
: "${BASE_URL:?set BASE_URL}"
RUNS="${RUNS:-3}"; COOLDOWN="${COOLDOWN:-60}"; RESULTS_DIR="${RESULTS_DIR:-results}"
mkdir -p "$RESULTS_DIR"
ulimit -n 250000 2>/dev/null || true
SUM="$RESULTS_DIR/holds-summary.txt"
for U in "$@"; do
  for i in $(seq 1 "$RUNS"); do
    echo "== $U users, run $i/$RUNS, $(date -u +%H:%M:%SZ)"
    K6_NO_USAGE_REPORT=true k6 run --quiet -e VUS="$U" -e LABEL="hold-r$i" -e RESULTS_DIR="$RESULTS_DIR" hold.js \
      > "$RESULTS_DIR/hold-$U-r$i.log" 2>&1
    rc=$?
    f=$(ls -t "$RESULTS_DIR"/hold-r"$i"-"$U"vu-*.json 2>/dev/null | head -1)
    if [ -n "$f" ]; then
      jq -r --arg u "$U" --arg i "$i" --arg rc "$rc" '.result | "\($u) users run \($i): \(if .pass then "PASS" else "FAIL" end)  rps=\(.rps|floor)  p95=\(.p95_ms|floor)  p99=\(.p99_ms|floor)  err=\(.error_rate*10000|floor/100)%  (k6 exit \($rc))"' "$f" | tee -a "$SUM"
    else
      echo "$U users run $i: no result file (k6 exit $rc); see $RESULTS_DIR/hold-$U-r$i.log" | tee -a "$SUM"
    fi
    sleep "$COOLDOWN"
  done
  jq -rs --arg u "$U" 'def med: sort | .[length/2|floor];
    map(.result) | "\($u) users MEDIAN of \(length): p95=\(map(.p95_ms)|med|floor)  p99=\(map(.p99_ms)|med|floor)  err=\(map(.error_rate)|med*10000|floor/100)%  passes=\(map(select(.pass))|length)/\(length)"' \
    $(ls -t "$RESULTS_DIR"/hold-r*-"$U"vu-*.json | head -"$RUNS") | tee -a "$SUM"
done
