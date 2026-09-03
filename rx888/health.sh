#!/bin/bash
# Quick RX888 HFDL health for cron/ops.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
ok=1
mb=$(cat /sys/module/usbcore/parameters/usbfs_memory_mb 2>/dev/null || echo missing)
echo "usbfs_memory_mb=$mb"
[[ "$mb" == "0" ]] || ok=0
if pgrep -x radiod >/dev/null; then
  echo "radiod ok pid=$(pgrep -x radiod | head -1)"
else
  echo "radiod DOWN"; ok=0
fi
n=$(pgrep -c -x dumphfdl || true)
echo "dumphfdl=$n"
[[ $n -ge 12 ]] || ok=0
lsusb -d 04b4:00f1 >/dev/null && echo "usb SuperSpeed 00f1 present" || { echo "RX888 not in app mode"; ok=0; }
python3 - <<PY
import sqlite3,time
con=sqlite3.connect("$_HUB/acars_data/messages.db")
now=int(time.time())
s=con.execute("select ifnull(sum(hfdl_count),0) from timeseries_stats where timestamp>?", (now-600,)).fetchone()[0]
print(f"hub_hfdl_10min={s}")
PY
exit $((1-ok))
