#!/usr/bin/env bash
# Prepares a fresh Ubuntu/Debian VM as a k6 load generator. Run as root (or with sudo) on the VM:
#   ssh root@LOADGEN 'bash -s' < setup-loadgen.sh
# Installs k6 from Grafana's apt repo, raises the open-file limit, widens the ephemeral port range and enables
# TIME_WAIT reuse, so one machine can hold tens of thousands of connections. Idempotent.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! command -v k6 >/dev/null; then
  apt-get update -q
  apt-get install -yq gnupg2 ca-certificates curl jq tmux
  gpg --no-default-keyring --keyring /usr/share/keyrings/k6-archive-keyring.gpg \
      --keyserver hkp://keyserver.ubuntu.com:80 --recv-keys C5AD17C747E3415A3642D57D77C6C491D6AC1D69
  echo "deb [signed-by=/usr/share/keyrings/k6-archive-keyring.gpg] https://dl.k6.io/deb stable main" \
      > /etc/apt/sources.list.d/k6.list
  apt-get update -q && apt-get install -yq k6
fi

cat > /etc/sysctl.d/90-loadgen.conf <<'SYSCTL'
net.ipv4.ip_local_port_range = 1024 65535
net.ipv4.tcp_tw_reuse = 1
net.core.somaxconn = 65535
net.ipv4.tcp_max_syn_backlog = 65535
fs.file-max = 2000000
SYSCTL
sysctl -q --system

cat > /etc/security/limits.d/90-loadgen.conf <<'LIMITS'
* soft nofile 250000
* hard nofile 250000
root soft nofile 250000
root hard nofile 250000
LIMITS

mkdir -p ~/loadtest/results
echo "k6 $(k6 version | head -1 | awk '{print $2}') ready on $(hostname) ($(nproc) vCPU, $(free -g | awk '/Mem/{print $2}') GB)."
echo "Log in again (or run 'ulimit -n 250000') before starting k6 so the new file limit applies."
