#!/bin/bash
# Unattended HF recovery. Safe to call from sdr-watch, udev, cron, or
# rx888-hfdl-watch. One instance at a time (flock).
#   1) usbfs_memory_mb drifted back to 16
#   2) radiod not running (USB move / abort / reboot)
#   3) radiod up but USB stalled ("No rx888 data")
#   4) pcmrecord / dumphfdl farm incomplete
# Does not restart ADS-B / VHF / AIS feeders.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
USBFS=/sys/module/usbcore/parameters/usbfs_memory_mb
LOG=$BASE/run/logs/radiod.log
LOCK=/run/rx888-health-fix.lock
# user fallback if /run is not writable
[[ -w /run ]] || LOCK=/tmp/rx888-health-fix.lock

exec 9>"$LOCK"
if ! flock -n 9; then
  echo "health-fix: already running"
  exit 0
fi

sysctl_cmd() {
  if [[ $(id -u) -eq 0 ]]; then
    systemctl "$@"
  else
    sudo -n systemctl "$@"
  fi
}

have_systemd() {
  systemctl is-enabled --quiet rx888-radiod.service 2>/dev/null
}

rx888_app() { lsusb -d 04b4:00f1 >/dev/null 2>&1; }
rx888_dfu() { lsusb -d 04b4:00f3 >/dev/null 2>&1; }

mb=$(cat "$USBFS" 2>/dev/null || echo missing)
if [[ "$mb" != "0" ]]; then
  echo "health-fix: usbfs_memory_mb=$mb — setting 0"
  if [[ -w $USBFS ]]; then
    echo 0 >"$USBFS"
  else
    echo 0 | sudo -n tee "$USBFS" >/dev/null
  fi
fi

if rx888_dfu && ! rx888_app; then
  echo "health-fix: RX888 in bootloader — wait for firmware"
  exit 0
fi
if ! rx888_app; then
  echo "health-fix: RX888 not on USB"
  exit 0
fi

start_farm() {
  if have_systemd; then
    echo "health-fix: start rx888-radiod + pcmrecord"
    sysctl_cmd start usbfs-memory.service rx888-radiod.service rx888-pcmrecord.service
  else
    echo "health-fix: systemd unit not enabled — start-rx888-hfdl.sh"
    RX888_FARM_LOCKED=1 bash "$BASE/start-rx888-hfdl.sh"
    return
  fi
  if [[ -x $BASE/ham/start-ham.sh ]]; then
    bash "$BASE/ham/start-ham.sh" || echo "health-fix: WARN ham start failed"
  fi
}

if ! pgrep -x radiod >/dev/null; then
  echo "health-fix: radiod not running — start"
  start_farm
  exit 0
fi

# Stream died but process lingered (seen 2026-08-13 after usbfs=16).
if [[ -f $LOG ]]; then
  last_quit=$(grep -n 'No rx888 data' "$LOG" | tail -1 | cut -d: -f1 || true)
  last_ok=$(grep -n 'rx888 running' "$LOG" | tail -1 | cut -d: -f1 || true)
  if [[ -n ${last_quit:-} ]]; then
    if [[ -z ${last_ok:-} || "$last_quit" -gt "${last_ok:-0}" ]]; then
      echo "health-fix: radiod idle after USB stall — restart"
      if have_systemd; then
        sysctl_cmd restart rx888-radiod.service
        sysctl_cmd start rx888-pcmrecord.service
      else
        RX888_FARM_LOCKED=1 bash "$BASE/restart-hfdl.sh"
      fi
      exit 0
    fi
  fi
fi

n=$(pgrep -c -x dumphfdl || true)
if [[ "${n:-0}" -lt 12 ]] && pgrep -x radiod >/dev/null; then
  if ! pgrep -x pcmrecord >/dev/null; then
    echo "health-fix: pcmrecord down (dumphfdl=$n) — start"
    if have_systemd; then
      sysctl_cmd start rx888-pcmrecord.service
    else
      RX888_FARM_LOCKED=1 bash "$BASE/start-rx888-hfdl.sh"
    fi
    exit 0
  fi
  etime=$(ps -o etimes= -C pcmrecord 2>/dev/null | awk '{print $1; exit}')
  if [[ -n ${etime:-} && "$etime" -gt 90 ]]; then
    echo "health-fix: dumphfdl=$n after pcmrecord ${etime}s — restart pcmrecord"
    if have_systemd; then
      sysctl_cmd restart rx888-pcmrecord.service || true
    fi
  fi
fi
exit 0
