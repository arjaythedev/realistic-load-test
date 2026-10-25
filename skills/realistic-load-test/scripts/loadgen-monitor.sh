#!/usr/bin/env bash
# Samples the load generator itself, so every result can show k6 was never the bottleneck. Linux and macOS.
#   nohup ./loadgen-monitor.sh 5 > /dev/null 2>&1 &        -> results/loadgen-resources.csv
#   pkill -f loadgen-monitor.sh                              (when the test is done)
# Columns: time, total CPU % (all cores), busiest core % (Linux only), k6 CPU % (of one core), k6 RSS MB,
#          memory used MB, established TCP connections. Keep total CPU under ~70 %; above that the client adds latency.
set -u
INTERVAL="${1:-5}"
CSV="${2:-results/loadgen-resources.csv}"
mkdir -p "$(dirname "$CSV")"
[ -f "$CSV" ] || echo "time,cpu_pct,max_core_pct,k6_cpu_pct,k6_rss_mb,mem_used_mb,tcp_estab" > "$CSV"

if [ "$(uname)" = Darwin ]; then
  while sleep "$INTERVAL"; do
    cpu=$(top -l 2 -n 0 -s 1 | awk '/CPU usage/{u=$3; s=$5} END{gsub("%","",u); gsub("%","",s); printf "%d", u+s}')
    read -r k6cpu k6rss <<<"$(ps -Ao %cpu=,rss=,comm= | awk '$3 ~ /(^|\/)k6$/ {c+=$1; r+=$2} END{printf "%d %d", c, r/1024}')"
    mem=$(vm_stat | awk '/page size/{ps=$8} /Pages active/{a=$3} /Pages wired/{w=$4} /occupied by compressor/{c=$5} END{printf "%d", (a+w+c)*ps/1048576}')
    est=$(netstat -an -p tcp 2>/dev/null | grep -c ESTABLISHED)
    echo "$(date +%H:%M:%S),$cpu,,$k6cpu,$k6rss,$mem,$est" >> "$CSV"
  done
fi

snap() { awk '/^cpu[0-9]* /{print $1, $2+$3+$4+$7+$8, $2+$3+$4+$5+$6+$7+$8+$9}' /proc/stat; }
k6j() { local t=0 j; for p in $(pgrep -x k6); do j=$(awk '{print $14+$15}' /proc/$p/stat 2>/dev/null) && t=$((t + j)); done; echo $t; }
HZ=$(getconf CLK_TCK)
s0="$(snap)"; k0=$(k6j)
while sleep "$INTERVAL"; do
  s1="$(snap)"; k1=$(k6j)
  read -r cpu maxc <<<"$(paste -d' ' <(echo "$s0") <(echo "$s1") | awk '{b=$5-$2; t=$6-$3; p=(t>0)?100*b/t:0; if($1=="cpu") tot=p; else if(p>m) m=p} END{printf "%d %d", tot, m}')"
  k6cpu=$(( (k1 - k0) * 100 / (HZ * INTERVAL) ))
  k6rss=$(ps -C k6 -o rss= 2>/dev/null | awk '{s+=$1} END{print int(s/1024)}')
  mem=$(awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{print int((t-a)/1024)}' /proc/meminfo)
  est=$(ss -Htn state established | wc -l)
  echo "$(date +%H:%M:%S),$cpu,$maxc,$k6cpu,${k6rss:-0},$mem,$est" >> "$CSV"
  s0="$s1"; k0=$k1
done
