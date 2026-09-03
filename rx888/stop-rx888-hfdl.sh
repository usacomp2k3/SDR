#!/bin/bash
set -u
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
if systemctl is-enabled --quiet rx888-radiod.service 2>/dev/null; then
  sudo -n systemctl stop rx888-pcmrecord.service rx888-radiod.service || {
    echo "systemctl stop failed; falling back to pids" >&2
  }
fi
RUN=$_HUB/rx888/run
for name in pcmrecord radiod; do
  if [[ -f $RUN/$name.pid ]]; then
    pid=$(cat "$RUN/$name.pid")
    if kill -0 "$pid" 2>/dev/null; then
      echo "Stopping $name pid $pid"
      kill "$pid" 2>/dev/null || true
      for i in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
      done
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$RUN/$name.pid"
  fi
done
pkill -f '/usr/local/bin/dumphfdl' 2>/dev/null || true
if [[ -x $_HUB/rx888/ham/stop-ham.sh ]]; then
  bash $_HUB/rx888/ham/stop-ham.sh || true
fi
echo "RX888 HFDL stopped."
