#!/usr/bin/env bash
# Pre/post cutover helpers for moving ADS-B from adsb-winterpark Pi → this host.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PI="${ADSB_PI_HOST:-adsb-winterpark}"
PI_TS="${ADSB_PI_TAILSCALE:-}"

echo "==> Hardware present?"
lsusb | grep -iE 'airspy|2838|Realtek' || true
echo
echo "Expected after move:"
echo "  Airspy Mini serial 10A862DC34914863 (AIRSPY_ADSB_SERIAL=0x10A862DC34914863)"
echo "  RTL serial 00000001 (UAT)"
echo

echo "==> Pi reachability (for globe_history rsync)"
if timeout 2 bash -c "echo >/dev/tcp/${PI_TS}/22" 2>/dev/null; then
  echo "  SSH port open on ${PI_TS}"
  echo "  Suggested rsync (run if you have keys):"
  echo "    rsync -avz root@${PI_TS}:/opt/adsb/ultrafeeder/globe_history/ ${ROOT}/adsb/ultrafeeder/globe_history/"
  echo "    rsync -avz root@${PI_TS}:/opt/adsb/ultrafeeder/graphs1090/ ${ROOT}/adsb/ultrafeeder/graphs1090/"
else
  echo "  Pi ${PI_TS} not reachable — using graphs1090 from Aug 8 backup already restored."
fi

echo
echo "==> graphs1090 RRD present?"
if [ -d "${ROOT}/adsb/ultrafeeder/graphs1090/rrd/localhost/dump1090-localhost" ]; then
  du -sh "${ROOT}/adsb/ultrafeeder/graphs1090/rrd/localhost"
  echo "  OK"
else
  echo "  MISSING — re-extract from adsb-feeder-config-*.backup"
fi

echo
echo "==> Cutover order"
cat <<EOF
  1. Stop feeders on the Pi (or power it off) so keys are not dual-used
  2. Unplug Airspy Mini + RTL 00000001 from Pi; plug into this host
  3. cd ${ROOT}
     sg docker -c 'docker compose -f docker-compose.adsb.yml pull'
     sg docker -c 'docker compose -f docker-compose.adsb.yml up -d'
  4. Check: docker logs airspy_adsb / dump978 / ultrafeeder
  5. Open http://127.0.0.1:8085/  and /graphs1090/
  6. Recreate acarshub if needed so ADSB_URL=http://ultrafeeder/...
     bash ~/acars-hub/refresh_all_the_things.sh   # or compose up for base + adsb
  7. Restart adsbvue: cd ~/adsbvue && sg docker -c 'docker compose up -d --force-recreate'
EOF
