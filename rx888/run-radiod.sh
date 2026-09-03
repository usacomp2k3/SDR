#!/bin/bash
# Foreground radiod for systemd. ka9q-radio can stop USB transfers
# ("No rx888 data for 5 seconds, quitting") and leave the process up —
# treat that as a crash so Restart=always actually fires.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
LOG=$BASE/run/logs/radiod.log
USBFS=/sys/module/usbcore/parameters/usbfs_memory_mb

mb=$(cat "$USBFS" 2>/dev/null || echo 16)
if [[ "$mb" != "0" ]]; then
  echo "usbfs_memory_mb=$mb (need 0). USBFS service did not apply." >&2
  exit 1
fi

mkdir -p "$BASE/run/logs"
export LD_LIBRARY_PATH="$BASE/opt/lib:${LD_LIBRARY_PATH:-}"
READY=$BASE/run/radiod.ready
rm -f "$READY"

"$BASE/opt/bin/radiod" -v -- "$BASE/run/radiod-hfdl.conf" >>"$LOG" 2>&1 &
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

(
  tail -n0 -F "$LOG" --pid="$rpid" 2>/dev/null \
    | grep -q --line-buffered 'static demodulators started'
  [[ $stopping -eq 0 ]] && touch "$READY"
) &
(
  tail -n0 -F "$LOG" --pid="$rpid" 2>/dev/null \
    | grep -q --line-buffered 'No rx888 data'
  if [[ $stopping -eq 0 ]]; then
    kill -TERM "$rpid" 2>/dev/null || true
  fi
) &

wait "$rpid" || true
[[ $stopping -eq 1 ]] && exit 0
cleanup
exit 1
