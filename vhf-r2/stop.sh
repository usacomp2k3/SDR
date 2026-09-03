#!/usr/bin/env bash
# Stop host VHF mux (systemd if present, else pidfiles).
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
if systemctl is-enabled --quiet vhf-r2-radiod.service 2>/dev/null \
  || systemctl is-active --quiet vhf-r2-radiod.service 2>/dev/null; then
  sudo -n systemctl stop vhf-r2-decode.service vhf-r2-radiod.service || {
    echo "Need sudo to stop vhf-r2 systemd units" >&2
    exit 1
  }
  echo "Stopped vhf-r2-radiod + vhf-r2-decode."
  exit 0
fi

BASE=$_HUB/vhf-r2
kill_pidfile() {
  local f="$1"
  [[ -f "$f" ]] || return 0
  local pid
  pid=$(cat "$f" 2>/dev/null || true)
  if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
    kill "$pid" 2>/dev/null || true
    for _ in $(seq 1 20); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 0.2
    done
    kill -9 "$pid" 2>/dev/null || true
  fi
  rm -f "$f"
}

kill_pidfile "$BASE/run/acars.pid"
kill_pidfile "$BASE/run/vdl2.pid"
kill_pidfile "$BASE/run/enrich.pid"
kill_pidfile "$BASE/run/radiod.pid"
echo "Stopped vhf-r2 mux pidfiles."
