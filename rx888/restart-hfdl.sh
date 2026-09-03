#!/bin/bash
# Restart radiod + pcmrecord without touching other station feeders.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
if [[ -z ${RX888_FARM_LOCKED:-} ]]; then
  LOCK=/run/rx888-health-fix.lock
  [[ -w /run ]] || LOCK=/tmp/rx888-health-fix.lock
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "restart-hfdl: already running"
    exit 0
  fi
  export RX888_FARM_LOCKED=1
fi
if ! lsusb -d 04b4:00f1 >/dev/null 2>&1 && ! lsusb -d 04b4:00f3 >/dev/null 2>&1; then
  echo "RX888 not on USB — skip HFDL restart"
  exit 0
fi
if systemctl is-enabled --quiet rx888-radiod.service 2>/dev/null; then
  sudo -n systemctl restart rx888-radiod.service
  # pcmrecord Requires= radiod; restart radiod tears it down — start it back
  sudo -n systemctl start rx888-pcmrecord.service
  echo "restarted via systemd"
  exit 0
fi

OPT=$BASE/opt
RUN=$BASE/run
export PATH="$OPT/bin:/usr/local/bin:$PATH"
export LD_LIBRARY_PATH="$OPT/lib:${LD_LIBRARY_PATH:-}"

stop_pid() {
  local f=$1
  [[ -f $f ]] || return 0
  local pid
  pid=$(cat "$f" || true)
  [[ -n ${pid:-} ]] || { rm -f "$f"; return 0; }
  if kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8; do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.4
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$f"
}

stop_pid "$RUN/pcmrecord.pid"
# children: dumphfdl started by pcmrecord --exec
# match only our station invocations
for pid in $(pgrep -x dumphfdl || true); do
  cmd=$(tr '\0' ' ' < /proc/$pid/cmdline 2>/dev/null || true)
  case "$cmd" in
    *WP-KMCO-HFDL*) kill "$pid" 2>/dev/null || true ;;
  esac
done
sleep 0.5
stop_pid "$RUN/radiod.pid"
sleep 1

# rotate live log so A/B windows are clean (keep history)
if [[ -s $RUN/logs/hfdl.log ]]; then
  mv "$RUN/logs/hfdl.log" "$RUN/logs/hfdl.log.$(date +%Y%m%dT%H%M%S)"
fi

nohup "$OPT/bin/radiod" -v -- "$RUN/radiod-hfdl.conf" \
  >>"$RUN/logs/radiod.log" 2>&1 &
echo $! >"$RUN/radiod.pid"

for i in $(seq 1 60); do
  if grep -q 'static demodulators started' <(tail -n 40 "$RUN/logs/radiod.log"); then
    break
  fi
  if ! kill -0 "$(cat "$RUN/radiod.pid")" 2>/dev/null; then
    echo "radiod died" >&2
    tail -20 "$RUN/logs/radiod.log" >&2
    exit 1
  fi
  sleep 1
done

nohup "$OPT/bin/pcmrecord" --raw -v \
  --exec "$BASE/start-hfdl \"\$k\" \"\$d\" \"\$r\"" \
  hfdl.local \
  >>"$RUN/logs/pcmrecord.log" 2>&1 &
echo $! >"$RUN/pcmrecord.pid"
sleep 3
echo "restarted radiod=$(cat $RUN/radiod.pid) pcmrecord=$(cat $RUN/pcmrecord.pid) dumphfdl=$(pgrep -c -x dumphfdl || echo 0)"
