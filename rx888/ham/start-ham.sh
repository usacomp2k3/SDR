#!/bin/bash
# Record + decode FT8/FT4/WSPR off the same radiod as HFDL.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/../.." && pwd)"
BASE=$_HUB/rx888
HAM=$BASE/ham
RUN=$BASE/run/ham
OPT=$BASE/opt
export PATH="$OPT/bin:/usr/local/bin:$PATH"
export LD_LIBRARY_PATH="$OPT/lib:${LD_LIBRARY_PATH:-}"
export PYTHONPATH="$HAM:$BASE/src/ftlib-pskreporter:${PYTHONPATH:-}"
# shellcheck disable=SC1091
source "$HAM/ham.env"

mkdir -p "$RUN/spool/ft8" "$RUN/spool/ft4" "$RUN/spool/wspr" "$RUN/logs"

alive() { [[ -f $1 ]] && kill -0 "$(cat "$1")" 2>/dev/null; }

start_one() {
  local name=$1 pidf=$2
  shift 2
  if alive "$pidf"; then
    echo "$name already running (pid $(cat "$pidf"))"
    return
  fi
  nohup "$@" >>"$RUN/logs/${name}.log" 2>&1 &
  echo $! >"$pidf"
  echo "started $name pid $(cat "$pidf")"
}

# Wait until radiod is advertising ham multicast (or just give it a few seconds)
for _ in $(seq 1 30); do
  pgrep -x radiod >/dev/null && break
  sleep 1
done

start_one pcm-ft8 "$RUN/pcm-ft8.pid" \
  "$OPT/bin/pcmrecord" -8 -d "$RUN/spool/ft8" ft8.local
start_one pcm-ft4 "$RUN/pcm-ft4.pid" \
  "$OPT/bin/pcmrecord" -4 -d "$RUN/spool/ft4" ft4.local
start_one pcm-wspr "$RUN/pcm-wspr.pid" \
  "$OPT/bin/pcmrecord" -w -d "$RUN/spool/wspr" wspr.local

start_one decode-ft8 "$RUN/decode-ft8.pid" \
  bash -c "stdbuf -oL $OPT/bin/decode_ft8 $RUN/spool/ft8 | tee -a $RUN/ft8.log | python3 -u $HAM/ingest.py --mode ft8"
start_one decode-ft4 "$RUN/decode-ft4.pid" \
  bash -c "stdbuf -oL $OPT/bin/decode_ft8 -4 $RUN/spool/ft4 | tee -a $RUN/ft4.log | python3 -u $HAM/ingest.py --mode ft4"
start_one decode-wspr "$RUN/decode-wspr.pid" \
  python3 -u "$HAM/wspr-loop.py"

export HF_CALLSIGN HF_GRID HF_ANTENNA HF_SOFTWARE
start_one psk-ft8 "$RUN/psk-ft8.pid" python3 -u "$HAM/psk-send.py" "$RUN/ft8.log" ft8
start_one psk-ft4 "$RUN/psk-ft4.pid" python3 -u "$HAM/psk-send.py" "$RUN/ft4.log" ft4
start_one psk-wspr "$RUN/psk-wspr.pid" python3 -u "$HAM/psk-send.py" "$RUN/wspr.log" wspr

echo "HAM FT8/FT4/WSPR started. Dashboard: http://127.0.0.1:8880/hf.html"
echo "PSK Reporter / WSPRnet as $HF_CALLSIGN @$HF_GRID"
