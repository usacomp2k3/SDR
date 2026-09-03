#!/bin/bash
# Foreground radiod for systemd. Exit if airspy aborts so Restart=always fires.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/vhf-r2
HUB=$_HUB
LOG=$BASE/run/logs/radiod.log
USBFS=/sys/module/usbcore/parameters/usbfs_memory_mb

mb=$(cat "$USBFS" 2>/dev/null || echo 16)
if [[ "$mb" != "0" ]]; then
  echo "usbfs_memory_mb=$mb (need 0). USBFS service did not apply." >&2
  exit 1
fi

mkdir -p "$BASE/run/logs" "$BASE/run/state"
export LD_LIBRARY_PATH="$HUB/rx888/opt/lib:${LD_LIBRARY_PATH:-}"
export PATH="$HUB/rx888/opt/bin:/usr/local/bin:/usr/bin:/bin"
READY=$BASE/run/radiod.ready
rm -f "$READY"

sg docker -c "docker update --restart=no acarsdec dumpvdl2" >/dev/null 2>&1 || true
sg docker -c "docker stop acarsdec dumpvdl2" >/dev/null 2>&1 || true

# Truncate so READY/abort watchers cannot match a previous run's banner.
: >"$LOG"
"$HUB/rx888/opt/bin/radiod" -v -- "$BASE/radiod.conf" >>"$LOG" 2>&1 &
rpid=$!
echo "$rpid" >"$BASE/run/radiod.pid"
stopping=0

cleanup() {
  kill "$rpid" 2>/dev/null || true
  wait "$rpid" 2>/dev/null || true
  rm -f "$BASE/run/radiod.pid" "$READY"
}
on_stop() {
  stopping=1
  cleanup
  exit 0
}
trap on_stop TERM INT

# Watchers must not inherit set -e/pipefail: grep miss would skip touch READY.
# Do not use tail -n0 — it races the start banner and decode then waits 90s.
set +e
(
  for _ in $(seq 1 240); do
    if grep -q -- 'static demodulators started' "$LOG" 2>/dev/null; then
      [[ $stopping -eq 0 ]] && touch "$READY"
      exit 0
    fi
    sleep 0.25
  done
) &
(
  tail -n0 -F "$LOG" --pid="$rpid" 2>/dev/null \
    | grep -q --line-buffered 'airspy has aborted'
  if [[ $stopping -eq 0 ]]; then
    kill -TERM "$rpid" 2>/dev/null || true
  fi
) &
set -e

wait "$rpid" || true
[[ $stopping -eq 1 ]] && exit 0
cleanup
exit 1
