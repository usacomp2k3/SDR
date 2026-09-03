#!/bin/bash
# Timed A/B window: apply variant, restart, count HFDL JSON for N seconds.
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
VAR=${1:?variant}
SECS=${2:-360}
SETTLE=${3:-45}
python3 "$BASE/apply-variant.py" "$VAR"
bash "$BASE/restart-hfdl.sh"
echo "settle ${SETTLE}s..."
sleep "$SETTLE"
# mark start by file mtime / line count after settle
LOG=$BASE/run/logs/hfdl.log
: >>"$LOG"
START_LINES=$(wc -l <"$LOG")
START_TS=$(date +%s)
sleep "$SECS"
END_TS=$(date +%s)
python3 - "$LOG" "$START_LINES" "$START_TS" "$END_TS" "$VAR" "$_HUB" <<'PY'
import json,sys,collections,pathlib
log, skip, t0, t1, var = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
hub = sys.argv[6]
freq=collections.Counter(); gs=collections.Counter(); ac=set(); n=0; snrs=[]
lines=log.read_text().splitlines()[skip:]
for line in lines:
    line=line.strip()
    if not line: continue
    try: j=json.loads(line)
    except: continue
    h=j.get("hfdl") or j
    n+=1
    fr=h.get("freq")
    if fr: freq[int(fr/1000 if fr>1e5 else fr)]+=1
    lp=h.get("lpdu") or {}
    name=(lp.get("dst") or {}).get("name")
    if name: gs[name]+=1
    src=lp.get("src") or {}
    if src.get("type")=="Aircraft" and src.get("id") is not None:
        ac.add(("id",src["id"]))
    fid=(lp.get("hfnpdu") or {}).get("flight_id")
    if fid: ac.add(("flt",fid))
    if h.get("sig_level") is not None and h.get("noise_level") is not None:
        snrs.append(h["sig_level"]-h["noise_level"])
dt=max(t1-t0,1)
snr=sum(snrs)/len(snrs) if snrs else 0
print(f"RESULT variant={var} secs={dt} msgs={n} mpm={n*60/dt:.2f} unique={len(ac)} mean_snr={snr:.1f} gs={gs.most_common(6)} freqs={freq.most_common(8)}")
pathlib.Path(hub + "/rx888/run/ab-results.txt").open("a").write(
    f"{var}\t{dt}\t{n}\t{n*60/dt:.3f}\t{len(ac)}\t{snr:.2f}\t{gs.most_common(4)}\n")
PY
