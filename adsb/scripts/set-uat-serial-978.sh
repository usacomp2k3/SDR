#!/usr/bin/env bash
# Set UAT RTL EEPROM serial to 978 (from 00000001 or verify already 978).
# USB descriptor still shows the old serial until you unplug/replug the stick.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

echo "Stopping RTL consumers..."
docker stop dump978 dumpvdl2 shipfeeder radiosonde_auto_rx 2>/dev/null || true
sleep 2

found=
already=
for i in 0 1 2 3 4 5; do
  out=$(rtl_eeprom -d "$i" 2>&1) || continue
  ser=$(echo "$out" | grep 'Serial number:' | head -1 | awk '{print $NF}')
  prod=$(echo "$out" | grep 'Product:' | head -1 | sed 's/.*Product:[[:space:]]*//')
  echo "index=$i product=$prod serial=$ser"
  if [ "$ser" = "00000001" ]; then
    found=$i
  elif [ "$ser" = "978" ]; then
    already=$i
  fi
done

if [ -n "${already:-}" ] && [ -z "${found:-}" ]; then
  echo "EEPROM already has serial 978 (index $already)."
  echo "If rtl_test still shows 00000001, unplug/replug the UAT dongle (front hub with 1090 Mini)."
elif [ -n "${found:-}" ]; then
  echo "Writing serial 978 to index $found"
  printf 'y\n' | rtl_eeprom -d "$found" -s 978
  echo "EEPROM write OK. Unplug and replug the UAT dongle now."
else
  echo "ERROR: no dongle with serial 00000001 or 978 openable."
  docker start dumpvdl2 shipfeeder radiosonde_auto_rx dump978 2>/dev/null || true
  exit 1
fi

# Point compose at 978
if grep -q '^UAT_RTL_SERIAL=' .env 2>/dev/null; then
  sed -i 's/^UAT_RTL_SERIAL=.*/UAT_RTL_SERIAL=978/' .env
else
  echo 'UAT_RTL_SERIAL=978' >> .env
fi
echo "UAT_RTL_SERIAL=978 in .env"

echo
echo "Next:"
echo "  1) Unplug + replug the UAT RTL (hub port next to Airspy Mini 1090)"
echo "  2) rtl_test -t   # should list SN: 978"
echo "  3) docker start dumpvdl2 shipfeeder radiosonde_auto_rx"
echo "  4) docker compose -f docker-compose.yml -f docker-compose.adsb.yml up -d --force-recreate dump978"
echo "  5) docker logs dump978 2>&1 | tail -20"
