#!/usr/bin/env bash
# One 1.05 MS/s IQ slice from ka9q pcmrecord stdin → NA extras + 136.700–136.975 (25 kHz).
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/vhf-r2
ROUTER=$(sg docker -c "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' acars_router" 2>/dev/null | awk 'NF{print; exit}')
ROUTER=${ROUTER:-172.18.0.4}
mkdir -p "$BASE/run/logs"
exec /usr/local/bin/dumpvdl2 \
  --iq-file - \
  --sample-format S16_LE \
  --oversample 10 \
  --centerfreq 136537500 \
  --station-id=WP-KMCO-VDL2 \
  --output "decoded:json:udp:address=${ROUTER},port=5555" \
  --output "decoded:json:file:path=${BASE}/run/logs/vdl2-decoded.log" \
  136100000 136300000 136650000 \
  136700000 136725000 136750000 136775000 136800000 136825000 \
  136850000 136875000 136900000 136925000 136950000 136975000
