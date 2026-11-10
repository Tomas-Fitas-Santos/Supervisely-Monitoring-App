#!/usr/bin/env bash
# Temporary IPv4 resolution workaround for WSL hosts with unreachable IPv6.
set -euo pipefail
cleanup() {
    python3 - <<'PY'
from pathlib import Path
path = Path('/etc/hosts')
path.write_text(''.join(line for line in path.read_text().splitlines(keepends=True)
                        if '# nightjar-benchmark-registry' not in line))
PY
}
trap cleanup EXIT
python3 - <<'PY'
from pathlib import Path
import socket
lines = []
for domain in ['registry-1.docker.io', 'auth.docker.io', 'production.cloudfront.docker.com']:
    addresses = sorted({row[4][0] for row in socket.getaddrinfo(domain, 443, socket.AF_INET)})
    lines.extend(f'{address} {domain} # nightjar-benchmark-registry\n' for address in addresses)
with Path('/etc/hosts').open('a') as stream:
    stream.write('\n' + ''.join(lines))
PY
sleep 6
bash "$(dirname "$0")/run_full_benchmark.sh" setup
