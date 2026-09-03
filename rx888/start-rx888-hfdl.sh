#!/bin/bash
# Start RX888mk2 ka9q-radio + all-band dumphfdl (WP-KMCO-HFDL).
# Prefers systemd units (usbfs-memory + rx888-radiod/pcmrecord) if enabled.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
if [[ -z ${RX888_FARM_LOCKED:-} ]]; then
  LOCK=/run/rx888-health-fix.lock
  [[ -w /run ]] || LOCK=/tmp/rx888-health-fix.lock
  exec 9>"$LOCK"
  if ! flock -n 9; then
    echo "start-rx888-hfdl: health-fix/start already running"
    exit 0
  fi
  export RX888_FARM_LOCKED=1
fi
OPT=$BASE/opt
RUN=$BASE/run
export PATH="$OPT/bin:/usr/local/bin:$PATH"
export LD_LIBRARY_PATH="$OPT/lib:${LD_LIBRARY_PATH:-}"

mkdir -p "$RUN/logs" "$RUN/state"

rx888_app() { lsusb -d 04b4:00f1 >/dev/null 2>&1; }
rx888_any() { rx888_app || lsusb -d 04b4:00f3 >/dev/null 2>&1; }

if ! rx888_any; then
  echo "RX888 not on USB — skip HFDL (no radiod wait)"
  exit 0
fi
if ! rx888_app; then
  echo "RX888 in bootloader (00f3) — waiting for firmware (45s)..."
  if ! bash "$BASE/wait-rx888.sh"; then
    echo "RX888 never reached app mode — skip HFDL"
    exit 0
  fi
fi

if [[ -f /sys/module/usbcore/parameters/usbfs_memory_mb ]]; then
  mb=$(cat /sys/module/usbcore/parameters/usbfs_memory_mb)
  if [[ "$mb" != "0" ]]; then
    echo "usbfs_memory_mb=$mb — trying to set 0"
    echo 0 | sudo -n tee /sys/module/usbcore/parameters/usbfs_memory_mb >/dev/null \
      || echo "WARN: could not set usbfs (need root: echo 0 > /sys/module/usbcore/parameters/usbfs_memory_mb)" >&2
  fi
fi

if systemctl is-enabled --quiet rx888-radiod.service 2>/dev/null; then
  if pgrep -x radiod >/dev/null && ! systemctl is-active --quiet rx888-radiod.service; then
    echo "legacy radiod already running; systemd takes over on next stop/reboot"
    exit 0
  fi
  sudo -n systemctl start usbfs-memory.service rx888-radiod.service rx888-pcmrecord.service
  echo "started via systemd (radiod + pcmrecord)"
  if [[ -x $BASE/ham/start-ham.sh ]]; then
    bash "$BASE/ham/start-ham.sh" || echo "WARN: ham FT8/FT4/WSPR start failed"
  fi
  exit 0
fi

# Park the HF+ docker decoder — it has no radio and would fight restart:always
if sg docker -c "docker inspect dumphfdl >/dev/null 2>&1"; then
  sg docker -c "docker update --restart=no dumphfdl" >/dev/null || true
  sg docker -c "docker stop dumphfdl" >/dev/null || true
fi

sudo -n ip link set lo multicast on 2>/dev/null || true

if [[ -f $RUN/radiod.pid ]] && kill -0 "$(cat "$RUN/radiod.pid")" 2>/dev/null; then
  if grep -q 'No rx888 data' "$RUN/logs/radiod.log" 2>/dev/null; then
    last_quit=$(grep -n 'No rx888 data' "$RUN/logs/radiod.log" | tail -1 | cut -d: -f1)
    last_ok=$(grep -n 'rx888 running' "$RUN/logs/radiod.log" | tail -1 | cut -d: -f1 || echo 0)
    if [[ "$last_quit" -gt "${last_ok:-0}" ]]; then
      echo "radiod pid alive but USB stalled — killing so we can restart"
      kill "$(cat "$RUN/radiod.pid")" 2>/dev/null || true
      sleep 1
      rm -f "$RUN/radiod.pid"
    fi
  fi
fi

if [[ -f $RUN/radiod.pid ]] && kill -0 "$(cat "$RUN/radiod.pid")" 2>/dev/null; then
  echo "radiod already running (pid $(cat "$RUN/radiod.pid"))"
else
  echo "Starting radiod (first FFT wisdom pass can take several minutes)..."
  nohup "$OPT/bin/radiod" -v -- "$RUN/radiod-hfdl.conf" \
    >"$RUN/logs/radiod.log" 2>&1 &
  echo $! >"$RUN/radiod.pid"
fi

echo "Waiting for radiod demodulators..."
demod_ok=0
for i in $(seq 1 60); do
  if grep -q 'static demodulators started' "$RUN/logs/radiod.log" 2>/dev/null; then
    demod_ok=1
    break
  fi
  if grep -q 'No rx888 data' "$RUN/logs/radiod.log" 2>/dev/null; then
    echo "radiod: no RX888 data — skip pcmrecord"
    break
  fi
  if [[ -f $RUN/radiod.pid ]] && ! kill -0 "$(cat "$RUN/radiod.pid")" 2>/dev/null; then
    echo "radiod died — see $RUN/logs/radiod.log"
    break
  fi
  if ! rx888_any; then
    echo "RX888 disappeared from USB — skip pcmrecord"
    break
  fi
  if (( i % 5 == 0 )); then
    echo "    still waiting (${i}/60)"
  fi
  sleep 2
done
if [[ $demod_ok -ne 1 ]]; then
  echo "WARN: radiod did not start demodulators — not starting dumphfdl farm"
  exit 0
fi

if [[ -f $RUN/pcmrecord.pid ]] && kill -0 "$(cat "$RUN/pcmrecord.pid")" 2>/dev/null; then
  echo "pcmrecord already running (pid $(cat "$RUN/pcmrecord.pid"))"
else
  echo "Starting pcmrecord → dumphfdl (13 band instances)..."
  nohup "$OPT/bin/pcmrecord" --raw -v \
    --exec "$BASE/start-hfdl \"\$k\" \"\$d\" \"\$r\"" \
    hfdl.local \
    >"$RUN/logs/pcmrecord.log" 2>&1 &
  echo $! >"$RUN/pcmrecord.pid"
fi

echo "PIDs: radiod=$(cat "$RUN/radiod.pid") pcmrecord=$(cat "$RUN/pcmrecord.pid")"
echo "Logs: $RUN/logs/"
echo "Watch decodes: tail -f $RUN/logs/hfdl.log"
if [[ -x $BASE/ham/start-ham.sh ]]; then
  bash "$BASE/ham/start-ham.sh" || echo "WARN: ham FT8/FT4/WSPR start failed"
fi
