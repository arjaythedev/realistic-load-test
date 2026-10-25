# Load generators

## Contents
- Do they need VMs at all?
- Sizing
- Creating VMs, per provider
- Setting up and running
- Several generators
- Teardown

## Do they need VMs at all?

| Situation | Where to run k6 |
|---|---|
| Smoke test, calibration, up to a few hundred users | The user's laptop is fine (wired network if possible) |
| Real capacity numbers | Cloud VMs in the target's region: stable network, no home router or Wi-Fi in the measurement, and the user's laptop stays free |
| They don't want to manage servers, or need traffic from many regions | Grafana Cloud k6 (`k6 cloud run journey…`). It's paid per VU-hour, has a free tier, and handles distribution and merged percentiles. handleSummary files aren't written there; read results in their UI |

## Sizing

Work it out from the profile and say it to the user:

- **Peak VUs** = the highest user count the test may reach (staircase MAX).
- **Peak RPS** = peak VUs × r.
- **Memory**: a sleepy realistic VU costs roughly 1–3 MB (more with big response bodies being read, TLS, or `http.batch`). Budget 3 MB/VU to be safe: 8 GB ≈ 2,500 VUs at worst, often 8–12k in practice. Measure on the smoke test: k6 RSS ÷ VUs.
- **CPU**: k6 handles very roughly 2,000–5,000 HTTPS req/s per vCPU for small responses. Sleepy users rarely make CPU the limit, unless r is high or TLS handshakes are frequent.
- **Connections**: each VU keeps ~1 connection (up to 6 during `http.batch`). One source IP can hold ~64k connections to one destination IP:port. Above ~25k connections per generator per target IP, add generators (or secondary IPs bound per scenario with `localIPs` in options).
- **Rule of thumb**: one 4 vCPU / 8 GB VM per ~8,000 sleepy users. Add 50 % headroom, because the generator must stay under 70 % CPU.

## Creating VMs, per provider

Ask which cloud the user already has an account and CLI for. If the CLI is installed and logged in, offer to run the commands. Otherwise give them the commands or console steps. Always state the approximate hourly cost (check the provider's current pricing page; a 4 vCPU VM is typically $0.05–0.20 per hour) and that VMs bill until deleted. Put generators in the same region as the target, or near its users for a CDN-fronted site. Use Ubuntu 24.04 and an SSH key.

**DigitalOcean**
```bash
doctl compute ssh-key list                                          # pick the key id
doctl compute droplet create lg1 --region nyc3 --size c-4 --image ubuntu-24-04-x64 --ssh-keys <KEY_ID> --wait \
  --format Name,PublicIPv4
doctl compute droplet delete lg1 -f                                 # teardown
```

**Hetzner Cloud** (cheapest; US regions `ash`, `hil`; EU `fsn1`, `nbg1`, `hel1`)
```bash
hcloud server create --name lg1 --type cpx31 --image ubuntu-24.04 --location ash --ssh-key <KEY_NAME>
hcloud server delete lg1
```

**AWS EC2**
```bash
aws ec2 run-instances --region us-east-1 --instance-type c7i.xlarge --key-name <KEY> \
  --image-id resolve:ssm:/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id \
  --security-group-ids <SG allowing SSH from your IP> --count 1 \
  --tag-specifications 'ResourceType=instance,Tags=[{Key=Name,Value=loadgen}]'
aws ec2 terminate-instances --instance-ids <ID>
```

**Google Cloud**
```bash
gcloud compute instances create lg1 --zone us-east1-b --machine-type c2d-standard-4 \
  --image-family ubuntu-2404-lts-amd64 --image-project ubuntu-os-cloud
gcloud compute instances delete lg1 --zone us-east1-b
```

If the target is on Vercel/Netlify/Cloudflare, the "region" is where the functions or origin run (Vercel functions default to `iad1`, Washington DC). Put the generator in the matching cloud region (us-east-1 / nyc / ash for `iad1`).

## Setting up and running

```bash
ssh root@LG 'bash -s' < <skill>/scripts/setup-loadgen.sh            # k6, file limits, port range
scp -r loadtest root@LG:~/                                           # journey.js, lib.js, hold.js, staircase.js, accounts.json
scp <skill>/scripts/{loadgen-monitor.sh,run-holds.sh} root@LG:~/loadtest/
ssh root@LG
tmux new -s lt                                                       # survives a dropped SSH connection
cd ~/loadtest && ulimit -n 250000 && mkdir -p results
nohup ./loadgen-monitor.sh 5 > /dev/null 2>&1 &
k6 run -e BASE_URL=https://… -e VUS=20 -e DURATION=30s -e RAMP_UP=5s hold.js     # smoke from the generator
```

Run k6 inside tmux and let it finish. You can follow along with `tail -f` on the log over a separate SSH call. Copy `results/` back to the user's `loadtest/results/` after each run, and pull the monitor CSV so the report can show generator CPU.

## Several generators

- Start them at the same moment (one SSH per generator, backgrounded, or `k6 run --paused` on each, then `k6 resume` on all).
- Split the users: each runs `VUS / G` (or `START/G`, `STEP/G`, `MAX/G` for the staircase). Set `LG_INDEX=0..G-1` so test accounts don't overlap.
- Combine results: users and RPS add up. Percentiles can't be averaged. Report the **worst generator's** p95/p99 (conservative), or stream all generators to one backend (`k6 run --out experimental-prometheus-rw` or Grafana Cloud k6) for exact merged percentiles. A staircase step fails if it fails on any generator.
- Generators in one region measure that region. Generators in several regions measure the global experience; report per region.

## Teardown

At the end of phase 6, give the exact delete commands from INFRA.md and offer to run them. Check the provider's console that nothing is left running. Remove the firewall allow-list rules added for the test.
