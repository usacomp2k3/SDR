#!/bin/bash
set -u
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/../.." && pwd)"
RUN=$_HUB/rx888/run/ham
for f in "$RUN"/*.pid; do
  [[ -f $f ]] || continue
  pid=$(cat "$f" 2>/dev/null || true)
  if [[ -n ${pid:-} ]] && kill -0 "$pid" 2>/dev/null; then
    echo "stopping $(basename "$f" .pid) $pid"
    kill "$pid" 2>/dev/null || true
    sleep 0.4
    kill -9 "$pid" 2>/dev/null || true
    # decode wrappers spawn decode_ft8 / tee
    pkill -P "$pid" 2>/dev/null || true
  fi
  rm -f "$f"
done
pkill -f '/rx888/opt/bin/decode_ft8' 2>/dev/null || true
pkill -f 'ingest.py --mode' 2>/dev/null || true
echo "HAM decoders stopped."
