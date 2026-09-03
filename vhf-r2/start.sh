#!/usr/bin/env bash
# Host VHF mux (R2 ka9q). Prefer systemd vhf-r2-radiod + vhf-r2-decode.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/vhf-r2
HUB=$_HUB
export LD_LIBRARY_PATH="$HUB/rx888/opt/lib:${LD_LIBRARY_PATH:-}"
export PATH="$HUB/rx888/opt/bin:/usr/local/bin:$PATH"
mkdir -p "$BASE/run/logs" "$BASE/run/state"

router_ip() {
  local ip
  ip=$(sg docker -c "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' acars_router" 2>/dev/null | awk 'NF{print; exit}')
  echo "${ip:-172.18.0.4}"
}

if [[ -f "$BASE/run/radiod.pid" ]] && kill -0 "$(cat "$BASE/run/radiod.pid")" 2>/dev/null; then
  echo "vhf-r2 radiod already running (pid $(cat "$BASE/run/radiod.pid"))" >&2
  exit 1
fi

if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  echo "==> park docker acarsdec / dumpvdl2 (USB frontends; keep acars_router)"
  sg docker -c "docker update --restart=no acarsdec dumpvdl2" >/dev/null
  sg docker -c "docker stop acarsdec dumpvdl2" >/dev/null

  echo "==> stop irdm so R2 is free, then it will come back on Mini *234f"
  sg docker -c "docker stop irdm" >/dev/null || true
fi

echo "==> start radiod on Airspy R2 35ac63dc2d86554f @ 138.0 MHz / 20 MS/s real"
: > "$BASE/run/logs/radiod.log"
"$HUB/rx888/opt/bin/radiod" -v -- "$BASE/radiod.conf" >>"$BASE/run/logs/radiod.log" 2>&1 &
echo $! > "$BASE/run/radiod.pid"

ready=0
for _ in $(seq 1 90); do
  if grep -q --line-buffered 'static demodulators started' "$BASE/run/logs/radiod.log" 2>/dev/null; then
    ready=1
    break
  fi
  if ! kill -0 "$(cat "$BASE/run/radiod.pid")" 2>/dev/null; then
    echo "radiod exited — last log:" >&2
    tail -40 "$BASE/run/logs/radiod.log" >&2
    exit 1
  fi
  sleep 1
done
if [[ "$ready" -ne 1 ]]; then
  echo "radiod did not report demodulators in 90s — last log:" >&2
  tail -40 "$BASE/run/logs/radiod.log" >&2
  exit 1
fi
echo "    radiod up (pid $(cat "$BASE/run/radiod.pid"))"

ROUTER=$(router_ip)
echo "==> acars_router ${ROUTER}"

echo "==> FANS enricher + ACARS pcmrecord (17 ch) + VDL2 pcmrecord (1.05 MS/s IQ)"
python3 -u "$HUB/acars/libacars-enrich.py" \
  --listen 0.0.0.0:15550 --forward "${ROUTER}:5550" \
  >>"$BASE/run/logs/enrich.log" 2>&1 &
echo $! > "$BASE/run/enrich.pid"

pcmrecord --raw -v --exec "$BASE/start-acars.sh" \
  vhf-acars.local >>"$BASE/run/logs/acars.log" 2>&1 &
echo $! > "$BASE/run/acars.pid"

pcmrecord --raw -v --exec "$BASE/start-vdl2.sh" \
  vhf-vdl2.local >>"$BASE/run/logs/vdl2.log" 2>&1 &
echo $! > "$BASE/run/vdl2.pid"

if [[ "${SKIP_DOCKER:-0}" != "1" ]]; then
  echo "==> irdm on Mini *234f (6 MS/s, partial L-band)"
  sg docker -c "docker start irdm" >/dev/null
fi

echo
echo "VHF mux running (R2 ka9q)."
echo "  radiod  $(cat "$BASE/run/radiod.pid")  $BASE/run/logs/radiod.log"
echo "  acars   $(cat "$BASE/run/acars.pid")  $BASE/run/logs/acars.log"
echo "  vdl2    $(cat "$BASE/run/vdl2.pid")   $BASE/run/logs/vdl2.log"
echo "  enrich  $(cat "$BASE/run/enrich.pid") $BASE/run/logs/enrich.log"
echo "  stop:   bash $BASE/stop.sh"
echo "  Persist units: sudo bash ~/acars-hub/refresh_all_the_things.sh --no-pull"
