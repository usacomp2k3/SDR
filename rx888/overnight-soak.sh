#!/bin/bash
# Overnight HFDL A/B: 3 long windows, then lock the winner.
# Logs: ~/acars-hub/rx888/run/overnight-soak.log
set -euo pipefail
_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_HUB="$(cd "$_HERE/.." && pwd)"
BASE=$_HUB/rx888
LOG=$BASE/run/overnight-soak.log
# ~2.5h each + settle → ~8h total
SECS=${1:-9000}
SETTLE=90
mkdir -p "$BASE/run/logs"
exec >>"$LOG" 2>&1
echo "======== overnight soak start $(date) window=${SECS}s ========"
: > "$BASE/run/ab-results-overnight.txt"
echo -e "variant\tsecs\tmsgs\tmpm\tunique\tmean_snr\tgs" >> "$BASE/run/ab-results-overnight.txt"

best_var=baseline
best_mpm=0
for VAR in baseline agc_slow gain18_att6; do
  echo "======== $VAR $(date) ========"
  # reuse ab-run but write overnight results
  python3 "$BASE/apply-variant.py" "$VAR"
  bash "$BASE/restart-hfdl.sh"
  echo "settle ${SETTLE}s..."
  sleep "$SETTLE"
  HLOG=$BASE/run/logs/hfdl.log
  : >>"$HLOG"
  START_LINES=$(wc -l <"$HLOG")
  START_TS=$(date +%s)
  sleep "$SECS"
  END_TS=$(date +%s)
  python3 - "$HLOG" "$START_LINES" "$START_TS" "$END_TS" "$VAR" "$_HUB" <<'PY'
import json,sys,collections,pathlib
log, skip, t0, t1, var = pathlib.Path(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
hub = sys.argv[6]
freq=collections.Counter(); gs=collections.Counter(); ac=set(); n=0; snrs=[]
for line in log.read_text().splitlines()[skip:]:
    line=line.strip()
    if not line: continue
    try: j=json.loads(line)
    except: continue
    h=j.get("hfdl") or j
    n+=1
    fr=h.get("freq")
    if fr: freq[int(fr/1000 if fr>1e5 else fr)]+=1
    name=((h.get("lpdu") or {}).get("dst") or {}).get("name")
    if name: gs[name]+=1
    src=(h.get("lpdu") or {}).get("src") or {}
    if src.get("type")=="Aircraft" and src.get("id") is not None:
        ac.add(("id",src["id"]))
    fid=((h.get("lpdu") or {}).get("hfnpdu") or {}).get("flight_id")
    if fid: ac.add(("flt",fid))
    if h.get("sig_level") is not None and h.get("noise_level") is not None:
        snrs.append(h["sig_level"]-h["noise_level"])
dt=max(t1-t0,1)
snr=sum(snrs)/len(snrs) if snrs else 0
mpm=n*60/dt
print(f"RESULT variant={var} secs={dt} msgs={n} mpm={mpm:.2f} unique={len(ac)} mean_snr={snr:.1f} gs={gs.most_common(6)}")
pathlib.Path(hub + "/rx888/run/ab-results-overnight.txt").open("a").write(
    f"{var}\t{dt}\t{n}\t{mpm:.3f}\t{len(ac)}\t{snr:.2f}\t{gs.most_common(4)}\n")
pathlib.Path("/tmp/hfdl-last-mpm").write_text(f"{var} {mpm}\n")
PY
  read -r v mpm </tmp/hfdl-last-mpm
  awk -v m="$mpm" -v v="$VAR" 'BEGIN{if(m+0>0) print v,m}' >/dev/null
  mpm_n=$(awk '{print $2}' /tmp/hfdl-last-mpm)
  if awk -v a="$mpm_n" -v b="$best_mpm" 'BEGIN{exit !(a>b)}'; then
    best_mpm=$mpm_n
    best_var=$VAR
  fi
done
echo "======== lock winner=$best_var mpm=$best_mpm $(date) ========"
python3 "$BASE/apply-variant.py" "$best_var"
bash "$BASE/restart-hfdl.sh"
echo "overnight soak done"
