#!/bin/bash
# Host ACARS + VDL2. Re-resolves acars_router on each start.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/vhf-r2
HUB=$_HUB
export LD_LIBRARY_PATH="$HUB/rx888/opt/lib:${LD_LIBRARY_PATH:-}"
export PATH="$HUB/rx888/opt/bin:/usr/local/bin:/usr/bin:/bin"
mkdir -p "$BASE/run/logs"

router_ip() {
  local ip
  ip=$(sg docker -c "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' acars_router" 2>/dev/null | awk 'NF{print; exit}')
  echo "${ip:-172.18.0.4}"
}

READY=$BASE/run/radiod.ready
for _ in $(seq 1 30); do
  if [[ -f $READY ]] && pgrep -f 'radiod .*vhf-r2/radiod.conf' >/dev/null; then
    break
  fi
  sleep 1
done
if [[ ! -f $READY ]]; then
  echo "vhf-r2 decode: radiod.ready missing — starting anyway" >&2
fi

ROUTER=$(router_ip)
echo "vhf-r2 decode: router=${ROUTER}"

python3 -u "$HUB/acars/libacars-enrich.py" \
  --listen 0.0.0.0:15550 --forward "${ROUTER}:5550" \
  >>"$BASE/run/logs/enrich.log" 2>&1 &
echo $! >"$BASE/run/enrich.pid"

pcmrecord --raw -v --exec "$BASE/start-acars.sh" \
  vhf-acars.local >>"$BASE/run/logs/acars.log" 2>&1 &
echo $! >"$BASE/run/acars.pid"

pcmrecord --raw -v --exec "$BASE/start-vdl2.sh" \
  vhf-vdl2.local >>"$BASE/run/logs/vdl2.log" 2>&1 &
echo $! >"$BASE/run/vdl2.pid"

pids=(
  "$(cat "$BASE/run/enrich.pid")"
  "$(cat "$BASE/run/acars.pid")"
  "$(cat "$BASE/run/vdl2.pid")"
)

cleanup() {
  for p in "${pids[@]}"; do
    kill "$p" 2>/dev/null || true
  done
  sleep 0.3
  for p in "${pids[@]}"; do
    kill -9 "$p" 2>/dev/null || true
  done
  rm -f "$BASE/run/enrich.pid" "$BASE/run/acars.pid" "$BASE/run/vdl2.pid"
}
stopping=0
on_stop() {
  stopping=1
  cleanup
  exit 0
}
trap on_stop TERM INT

wait -n "${pids[@]}" || true
[[ $stopping -eq 1 ]] && exit 0
cleanup
exit 1
