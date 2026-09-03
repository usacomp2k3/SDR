#!/bin/bash
# pcmrecord → 13× start-hfdl. Wait until this boot's radiod is serving.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
export PATH="$BASE/opt/bin:/usr/local/bin:$PATH"
export LD_LIBRARY_PATH="$BASE/opt/lib:${LD_LIBRARY_PATH:-}"

# Wait for *this* radiod start (ready file), not a leftover log line.
for _ in $(seq 1 90); do
  if pgrep -x radiod >/dev/null && [[ -f $BASE/run/radiod.ready ]]; then
    break
  fi
  if ! lsusb -d 04b4:00f1 >/dev/null 2>&1 && ! lsusb -d 04b4:00f3 >/dev/null 2>&1; then
    echo "RX888 not on USB — not starting pcmrecord" >&2
    exit 0
  fi
  sleep 1
done

exec "$BASE/opt/bin/pcmrecord" --raw -v \
  --exec "$BASE/start-hfdl \"\$k\" \"\$d\" \"\$r\"" \
  hfdl.local
