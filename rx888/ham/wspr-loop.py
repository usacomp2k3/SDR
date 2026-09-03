#!/usr/bin/env python3
"""Watch pcmrecord WSPR 120 s wavs, run wsprd, ingest, delete."""
from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path

from ingest import _db, _insert, parse_wspr

_HUB = str(Path(__file__).resolve().parents[2])
SPOOL = Path(str(_HUB) + "/rx888/run/ham/spool/wspr")
LOG = Path(str(_HUB) + "/rx888/run/ham/wspr.log")
ENV = Path(str(_HUB) + "/rx888/ham/ham.env")
WSPRD = "/usr/bin/wsprd"
WSPRNET = "http://wsprnet.org/post"


def _env(key: str, default: str = "") -> str:
    if not ENV.exists():
        return default
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return default
# yyyymmddThhmmssZ_ffffffff_usb.wav  (freq in Hz)
FNAME = re.compile(r"_(\d{5,12})_")


def freq_from_name(p: Path) -> float | None:
    m = FNAME.search(p.name)
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def process(wav: Path) -> None:
    freq_hz = freq_from_name(wav)
    freq_mhz = (freq_hz / 1e6) if freq_hz else 14.0956
    cmd = [WSPRD, "-w", "-f", f"{freq_mhz:.6f}", str(wav)]
    proc = subprocess.run(cmd, cwd=str(wav.parent), capture_output=True, text=True, timeout=90)
    out = (proc.stdout or "") + (proc.stderr or "")
    con = _db()
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as lf:
        for line in out.splitlines():
            row = parse_wspr(line, freq_hint_hz=freq_hz)
            if row:
                _insert(con, row)
                lf.write(line + "\n")
                print(line, flush=True)
    _upload_wsprnet(wav.parent)
    wav.unlink(missing_ok=True)
    for extra in wav.parent.glob("*.c2"):
        extra.unlink(missing_ok=True)
    for extra in wav.parent.glob("hashtable.txt"):
        extra.unlink(missing_ok=True)


def _upload_wsprnet(spool: Path) -> None:
    call = _env("HF_HAM_CALL") or _env("HF_CALLSIGN")
    grid = _env("HF_GRID", "EL98ho")
    if not call or "-" in call:
        return
    mept = spool / "ALL_WSPR.TXT"
    if not mept.exists() or mept.stat().st_size == 0:
        return
    try:
        proc = subprocess.run(
            [
                "curl",
                "-sS",
                "-m",
                "25",
                "-F",
                f"allmept=@{mept}",
                "-F",
                f"call={call}",
                "-F",
                f"grid={grid}",
                WSPRNET,
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        print(f"wsprnet: http {proc.returncode} {proc.stdout.strip()[:120]}", flush=True)
        if proc.returncode == 0:
            mept.write_text("")
    except Exception as e:
        print(f"wsprnet: {e}", file=sys.stderr, flush=True)


def main() -> int:
    SPOOL.mkdir(parents=True, exist_ok=True)
    seen: set[str] = set()
    while True:
        files = sorted(SPOOL.rglob("*.wav"))
        now = time.time()
        for wav in files:
            try:
                age = now - wav.stat().st_mtime
            except OSError:
                continue
            key = str(wav)
            if key in seen:
                continue
            # file still growing if pcmrecord has it open
            if age < 3:
                continue
            seen.add(key)
            try:
                process(wav)
            except Exception as e:
                print(f"wspr-loop: {wav}: {e}", file=sys.stderr, flush=True)
        if len(seen) > 5000:
            seen.clear()
        time.sleep(2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
