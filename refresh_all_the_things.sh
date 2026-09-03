#!/usr/bin/env bash
# WP-KMCO station bring-up / confirm.
#   sudo bash ~/acars-hub/refresh_all_the_things.sh           # apt + pull + up + settle + verify
#   sudo bash ~/acars-hub/refresh_all_the_things.sh --no-pull # apt + confirm local compose/scripts
#   sudo bash ~/acars-hub/refresh_all_the_things.sh --no-apt  # skip apt-get
# This is the only day-to-day station start/confirm script.
#
# Order matters: compose up → wait for acars_router → park docker
# acarsdec/dumpvdl2 → host VHF mux (R2 ka9q) re-resolves router → host
# HFDL last → print a check. Do not extra-restart irdm (USB / Mini);
# sdr-watch.timer reopens a decoder after a USB reset.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
STATION_USER="${SUDO_USER:-}"
if [[ -z ${STATION_USER} || ${STATION_USER} == root ]]; then
  STATION_USER="$(id -un 1000 2>/dev/null || true)"
fi
HOME_DIR="$(getent passwd "${STATION_USER:-}" 2>/dev/null | cut -d: -f6)"
HOME_DIR="${HOME_DIR:-${HOME:-}}"
cd "$ROOT"

PULL=1
APT=1
PASSTHRU=()
for arg in "$@"; do
  case "$arg" in
    --no-pull|-n) PULL=0; PASSTHRU+=(--no-pull) ;;
    --no-apt) APT=0; PASSTHRU+=(--no-apt) ;;
    --skip-apt) APT=0 ;;
    -h|--help)
      sed -n '2,9p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $arg (try --no-pull / --no-apt)" >&2
      exit 2
      ;;
  esac
done

install_subst() {
  # Git units use /home/station + User=station; install the real account.
  local src=$1 dest=$2 mode=$3
  local tmp
  tmp=$(mktemp)
  sed -e "s|/home/station|${HOME_DIR}|g" \
      -e "s|User=station|User=${STATION_USER}|g" \
      -e "s|Group=station|Group=${STATION_USER}|g" \
      -e "s|^station ALL=|${STATION_USER} ALL=|" \
      "$src" >"$tmp"
  install -m "$mode" "$tmp" "$dest"
  rm -f "$tmp"
}

install_sdr_watch() {
  # Must run as root. refresh drops to $SUDO_USER after apt, so call this first.
  local src=$ROOT/sdr-watch
  local rx=$ROOT/rx888/systemd
  [[ -f $src/sdr-watch.service ]] || return 0
  echo "==> persist sdr-watch (system timer + udev only) + RX888 units"
  install_subst "$src/sdr-watch.service" /etc/systemd/system/sdr-watch.service 644
  install -m 644 "$src/sdr-watch.timer" /etc/systemd/system/sdr-watch.timer
  install -m 644 "$src/99-sdr-watch.rules" /etc/udev/rules.d/99-sdr-watch.rules
  if [[ -d $rx ]]; then
    install_subst "$rx/rx888-radiod.service" /etc/systemd/system/rx888-radiod.service 644
    install_subst "$rx/rx888-pcmrecord.service" /etc/systemd/system/rx888-pcmrecord.service 644
    install_subst "$rx/rx888-hfdl-watch.service" /etc/systemd/system/rx888-hfdl-watch.service 644
    install -m 644 "$rx/rx888-hfdl-watch.timer" /etc/systemd/system/rx888-hfdl-watch.timer
  fi
  local vhf=$ROOT/vhf-r2/systemd
  if [[ -d $vhf ]]; then
    install_subst "$vhf/vhf-r2-radiod.service" /etc/systemd/system/vhf-r2-radiod.service 644
    install_subst "$vhf/vhf-r2-decode.service" /etc/systemd/system/vhf-r2-decode.service 644
    # sudoers.d ignores filenames with a dot — install without extension.
    if [[ -f $vhf/sudoers-wp-kmco-vhf-r2 ]]; then
      install_subst "$vhf/sudoers-wp-kmco-vhf-r2" /etc/sudoers.d/wp-kmco-vhf-r2 440
      if ! visudo -cf /etc/sudoers.d/wp-kmco-vhf-r2 >/dev/null; then
        rm -f /etc/sudoers.d/wp-kmco-vhf-r2
        echo "WARN: vhf-r2 sudoers rejected by visudo" >&2
      fi
    fi
  fi
  systemctl daemon-reload
  systemctl enable --now sdr-watch.timer
  systemctl enable rx888-radiod.service rx888-pcmrecord.service rx888-hfdl-watch.timer
  systemctl enable vhf-r2-radiod.service vhf-r2-decode.service
  systemctl start rx888-hfdl-watch.timer || true
  systemctl start vhf-r2-radiod.service vhf-r2-decode.service || true
  udevadm control --reload-rules || true
  # Dual user+system timers used to start two radiods on one stick.
  local u
  for u in "${SUDO_USER:-}" "${STATION_USER:-}"; do
    [[ -n $u && $u != root ]] || continue
    sudo -u "$u" XDG_RUNTIME_DIR="/run/user/$(id -u "$u")" \
      systemctl --user disable --now sdr-watch.timer 2>/dev/null || true
    sudo -u "$u" XDG_RUNTIME_DIR="/run/user/$(id -u "$u")" \
      systemctl --user mask sdr-watch.timer 2>/dev/null || true
  done
}

if [[ $APT -eq 1 ]]; then
  if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "apt-get needs root: sudo bash $0 $*" >&2
    exit 1
  fi
  echo "==> [apt] apt-get update"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update
  echo "==> [apt] apt-get upgrade -y"
  apt-get upgrade -y
  install_sdr_watch
  if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != "root" ]]; then
    echo "==> [apt] dropping back to $SUDO_USER for station bring-up"
    exec sudo -u "$SUDO_USER" -H bash "$0" --skip-apt "${PASSTHRU[@]}"
  fi
elif [[ ${EUID:-$(id -u)} -eq 0 ]]; then
  install_sdr_watch
fi

if [[ ${EUID:-$(id -u)} -eq 0 && -z ${HOME_DIR} ]]; then
  HOME_DIR="$(getent passwd "${STATION_USER:-$(id -un 1000)}" | cut -d: -f6)"
fi

mkdir -p acars_data irdm-logs hfdl-data hfdl-scanner ais-data radiosonde/log \
  adsb/ultrafeeder/graphs1090 adsb/ultrafeeder/globe_history adsb/dump978 \
  portal/map-data

d() { sg docker -c "$*"; }

ACARS_COMPOSE=(
  -f docker-compose.yml
  -f docker-compose.irdm.yml
  -f docker-compose.acars.yml
  -f docker-compose.vdl2.yml
  -f docker-compose.hfdl.yml
  -f docker-compose.extras.yml
  -f docker-compose.adsb.yml
  -f docker-compose.portal.yml
)

wait_up() {
  local name=$1
  local secs=${2:-60}
  local i
  for i in $(seq 1 "$secs"); do
    if d "docker inspect -f '{{.State.Running}}' $(printf %q "$name")" 2>/dev/null | grep -q true; then
      return 0
    fi
    sleep 1
  done
  echo "WARN: $name not running after ${secs}s" >&2
  return 1
}

router_ip() {
  d "docker inspect -f '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}' acars_router" \
    | awk 'NF{print; exit}'
}

echo "==> [0] park XNG if present"
if systemctl list-unit-files xng-station.service 2>/dev/null | grep -q xng-station; then
  sudo -n systemctl disable --now xng-station.service 2>/dev/null || true
fi

# ── 1) Docker radio stack + portal ──────────────────────────────────────────
if [[ $PULL -eq 1 ]]; then
  echo "==> [1] Pulling acars-hub images..."
  d "cd $(printf %q "$ROOT") && docker compose ${ACARS_COMPOSE[*]} pull"
else
  echo "==> [1] SKIP pull (--no-pull)"
fi

echo "==> [1] compose up -d"
d "cd $(printf %q "$ROOT") && docker compose ${ACARS_COMPOSE[*]} up -d"

echo "==> [1] wait for acars_router"
wait_up acars_router 45 || true
RIP=$(router_ip || true)
echo "    acars_router IP=${RIP:-unknown}"

# Live VHF is host ka9q (R2). Docker acarsdec/dumpvdl2 are profile
# legacy-usb — park any leftover containers so they cannot steal Mini/R2.
echo "==> [1b] park docker acarsdec/dumpvdl2 (host VHF mux owns those modes)"
d "docker update --restart=no acarsdec dumpvdl2" 2>/dev/null || true
d "docker stop acarsdec dumpvdl2" 2>/dev/null || true

echo "==> [1c] host VHF mux (R2 ka9q → acarsdec/dumpvdl2)"
if sudo -n systemctl restart vhf-r2-decode.service 2>/dev/null; then
  sudo -n systemctl start vhf-r2-radiod.service vhf-r2-decode.service 2>/dev/null || true
elif systemctl is-enabled --quiet vhf-r2-decode.service 2>/dev/null; then
  echo "    WARN: could not restart vhf-r2-decode (need sudo -n)"
else
  echo "    WARN: vhf-r2-decode.service not enabled — run refresh as root once"
fi

# Hub ZMQ clients die when the router container is recreated.
echo "==> [1d] reconnect ACARS Hub listeners"
if d "docker inspect acarshub >/dev/null 2>&1"; then
  d "docker restart acarshub" || echo "    WARN: acarshub restart failed"
  wait_up acarshub 30 || true
fi

# ── 2) adsbvue ──────────────────────────────────────────────────────────────
if [[ -f "$HOME_DIR/adsbvue/docker-compose.yml" ]]; then
  echo "==> [2] adsbvue pull+up..."
  if [[ $PULL -eq 1 ]]; then
    d "cd $(printf %q "$HOME_DIR/adsbvue") && docker compose pull && docker compose up -d"
  else
    d "cd $(printf %q "$HOME_DIR/adsbvue") && docker compose up -d"
  fi
else
  echo "==> [2] SKIP adsbvue"
fi

# ── 3) Meshtastic USB→TCP bridge ────────────────────────────────────────────
echo "==> [3] mesh bridge :4403"
if systemctl --user list-unit-files 2>/dev/null | grep -q meshtastic-socat-bridge.service; then
  systemctl --user enable --now meshtastic-socat-bridge.service 2>/dev/null \
    || systemctl --user restart meshtastic-socat-bridge.service 2>/dev/null \
    || true
fi
if ! ss -ltn 2>/dev/null | grep -q ':4403'; then
  if [[ -x "$HOME_DIR/meshmonitor/run-socat-loop.sh" ]]; then
    echo "    starting run-socat-loop.sh"
    nohup bash "$HOME_DIR/meshmonitor/run-socat-loop.sh" \
      >>"$HOME_DIR/meshmonitor/socat-bridge.log" 2>&1 &
    sleep 1
  fi
fi

# ── 4) MeshMonitor ──────────────────────────────────────────────────────────
if [[ -f "$HOME_DIR/meshmonitor/docker-compose.yml" ]]; then
  echo "==> [4] meshmonitor..."
  if [[ $PULL -eq 1 ]]; then
    d "cd $(printf %q "$HOME_DIR/meshmonitor") && docker compose pull && docker compose up -d"
  else
    d "cd $(printf %q "$HOME_DIR/meshmonitor") && docker compose up -d"
  fi
else
  echo "==> [4] SKIP meshmonitor"
fi

# ── 5) Host RX888 HFDL last — start-hfdl re-resolves acars_router IP ───────
echo "==> [5] RX888 HFDL"
RIP=$(router_ip || true)
HFD_IPS=$(pgrep -a dumphfdl 2>/dev/null | grep -oE 'address=[0-9.]+' | sed 's/address=//' | sort -u | tr '\n' ' ' || true)
echo "    router=${RIP:-?}  dumphfdl dests=${HFD_IPS:-none}"
if ! lsusb -d 04b4:00f1 >/dev/null 2>&1 && ! lsusb -d 04b4:00f3 >/dev/null 2>&1; then
  echo "    RX888 not on USB — skip HFDL start (no 6-min radiod wait)"
elif [[ -n "${RIP:-}" && -n "${HFD_IPS:-}" && "$HFD_IPS" != *"$RIP"* ]]; then
  echo "    HFDL aimed at old router IP — restarting decoder farm"
  if [[ -x $ROOT/rx888/restart-hfdl.sh ]]; then
    bash "$ROOT/rx888/restart-hfdl.sh" || echo "    WARN: restart-hfdl failed"
  fi
elif [[ -x $ROOT/rx888/start-rx888-hfdl.sh ]]; then
  bash "$ROOT/rx888/start-rx888-hfdl.sh" || echo "    WARN: RX888 HFDL start failed"
fi

# ── 6) Confirm ──────────────────────────────────────────────────────────────
echo
echo "==> [6] confirm"
FAIL=0
check() {
  local ok=$1
  local msg=$2
  if [[ $ok -eq 0 ]]; then
    echo "    OK   $msg"
  else
    echo "    FAIL $msg"
    FAIL=1
  fi
}

d "docker ps --format 'table {{.Names}}\t{{.Status}}'" || true
echo

if pgrep -f 'libacars-enrich.py --listen 0.0.0.0:15550' >/dev/null; then
  check 0 "host VHF FANS enricher"
else
  check 1 "host VHF FANS enricher not running"
fi
if pgrep -f 'dumpvdl2 --iq-file' >/dev/null; then
  check 0 "host dumpvdl2 (ka9q IQ)"
else
  check 1 "host dumpvdl2 not running"
fi
if pgrep -f 'radiod .*vhf-r2/radiod.conf' >/dev/null; then
  check 0 "vhf-r2 radiod (Airspy R2)"
else
  check 1 "vhf-r2 radiod not running"
fi
if d "docker inspect -f '{{.State.Running}}' acarsdec" 2>/dev/null | grep -q true; then
  check 1 "docker acarsdec running (must stay parked — steals Mini from irdm)"
else
  check 0 "docker acarsdec parked"
fi
if systemctl is-enabled --quiet vhf-r2-radiod.service 2>/dev/null; then
  check 0 "vhf-r2-radiod.service enabled"
else
  check 1 "vhf-r2-radiod.service not enabled"
fi

if d "docker exec irdm ps -eo args" 2>/dev/null | grep -q -- '-m libacars'; then
  check 0 "irdm reassembler -m libacars"
else
  check 1 "irdm not on -m libacars"
fi

if d "docker inspect -f '{{.State.Running}}' acars2pos" 2>/dev/null | grep -q true; then
  check 0 "acars2pos (ADS-C → tar1090 :32009)"
else
  check 1 "acars2pos not running"
fi

RIP=$(router_ip || true)
HFD_IPS=$(pgrep -a dumphfdl 2>/dev/null | grep -oE 'address=[0-9.]+' | sed 's/address=//' | sort -u | tr '\n' ' ' || true)
if ! lsusb -d 04b4:00f1 >/dev/null 2>&1 && ! lsusb -d 04b4:00f3 >/dev/null 2>&1; then
  check 0 "HFDL skipped (RX888 not on USB)"
elif [[ -n "${RIP:-}" && "$HFD_IPS" == *"$RIP"* ]]; then
  check 0 "dumphfdl → acars_router $RIP"
elif pgrep -x dumphfdl >/dev/null; then
  check 1 "dumphfdl dest '${HFD_IPS:-?}' != router ${RIP:-?}"
else
  check 1 "no dumphfdl processes (RX888 is on USB)"
fi

if systemctl is-active --quiet sdr-watch.timer 2>/dev/null; then
  check 0 "sdr-watch.timer (system USB decoder reopen)"
else
  check 1 "sdr-watch.timer not active"
fi
if systemctl --user is-active --quiet sdr-watch.timer 2>/dev/null; then
  check 1 "user sdr-watch.timer still active (fights system timer)"
else
  check 0 "user sdr-watch.timer off"
fi
if systemctl is-enabled --quiet rx888-radiod.service 2>/dev/null; then
  check 0 "rx888-radiod.service enabled"
else
  check 1 "rx888-radiod.service not enabled"
fi

if curl -fsS --max-time 4 http://127.0.0.1:8881/api/status >/dev/null; then
  check 0 "status API :8881"
else
  check 1 "status API :8881 not answering"
fi

python3 - "$ROOT/acars_data/messages.db" <<'PY' || true
import sqlite3, sys, time
p = sys.argv[1]
try:
    con = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
except Exception as e:
    print("    skip hub db:", e)
    raise SystemExit(0)
now = int(time.time())
print("    hub last 2 min:")
for mt in ("ACARS", "VDL-M2", "HFDL", "IRDM"):
    n, last, la = con.execute(
        """SELECT COUNT(*), datetime(MAX(msg_time),'unixepoch'),
                  SUM(CASE WHEN libacars IS NOT NULL AND trim(libacars)!='' THEN 1 ELSE 0 END)
           FROM messages WHERE message_type=? AND msg_time>?""",
        (mt, now - 120),
    ).fetchone()
    print(f"      {mt:8} n={n or 0:<4} libacars={la} last={last}")
PY

echo
echo "Dashboards:"
echo "  Station portal: http://127.0.0.1:8880/"
echo "  Status API:     http://127.0.0.1:8881/api/status"
echo "  ACARS Hub:      http://127.0.0.1:8080"
echo "  tar1090:        http://127.0.0.1:8085"
echo "  graphs1090:     http://127.0.0.1:8085/graphs1090/"
echo "  Iridium map:    http://127.0.0.1:8888"
echo "  AIS map:        http://127.0.0.1:8090"
echo "  Radiosonde:     http://127.0.0.1:5000"
echo "  UAT 978:        http://127.0.0.1:9780"
echo "  FR24:           http://127.0.0.1:8754"
echo "  adsbvue:        http://127.0.0.1:24556"
echo "  MeshMonitor:    http://127.0.0.1:3001"
echo
echo "Station IDs: WP-KMCO-ACARS / -VDL2 / -IRDM / -HFDL / -AIS / -SONDE"
if [[ $FAIL -ne 0 ]]; then
  echo "CONFIRM: some checks failed (see FAIL lines above)"
  exit 1
fi
echo "CONFIRM: ordered settle + checks passed"
