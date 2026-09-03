#!/usr/bin/env python3
"""Parse decode_ft8 / wsprd lines → spots.db (for the coverage map + HF dashboard)."""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

from maidenhead import from_maiden

_HUB = str(Path(__file__).resolve().parents[2])
DB = Path(str(_HUB) + "/rx888/run/ham/spots.db")

FTX = re.compile(
    r"(?P<ts>\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<score>-?\d+)\s+(?P<dt>[-+0-9.]+)\s+(?P<freq>[0-9,.]+)\s+~\s+(?P<msg>.*)$"
)
GRID = re.compile(r"\b([A-R]{2}[0-9]{2}(?:[A-X]{2})?)\b")
CALL = re.compile(r"\b([A-Z0-9]{1,3}[0-9][A-Z0-9]{1,4}(?:/[A-Z0-9]{1,4})?)\b")
WSPR = re.compile(
    r"(?:(?P<ymd>\d{6})\s+)?(?P<hhmm>\d{4})\s+"
    r"(?P<dt>[-0-9.]+)\s+(?P<snr>-?\d+)\s+(?P<drift>[-0-9.]+)\s+"
    r"(?P<freq>[0-9.]+)\s+(?P<call>\S+)\s+(?P<grid>[A-R]{2}[0-9]{2})\s+(?P<dbm>\d+)"
)


def _db() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(DB), timeout=10)
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS spots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            t REAL NOT NULL,
            mode TEXT NOT NULL,
            freq_hz REAL,
            snr INTEGER,
            callsign TEXT,
            grid TEXT,
            lat REAL,
            lon REAL,
            msg TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_spots_t ON spots(t)")
    con.execute("CREATE INDEX IF NOT EXISTS idx_spots_mode_t ON spots(mode, t)")
    return con


def _insert(con: sqlite3.Connection, row: dict) -> None:
    con.execute(
        """INSERT INTO spots(t, mode, freq_hz, snr, callsign, grid, lat, lon, msg)
           VALUES (:t, :mode, :freq_hz, :snr, :callsign, :grid, :lat, :lon, :msg)""",
        row,
    )
    con.commit()


def parse_ftx(line: str, mode: str) -> dict | None:
    m = FTX.search(line.strip())
    if not m:
        return None
    ts = datetime.strptime(m.group("ts"), "%Y/%m/%d %H:%M:%S").replace(tzinfo=timezone.utc)
    msg = m.group("msg").strip()
    freq = float(m.group("freq").replace(",", ""))
    grid = None
    call = None
    for g in GRID.findall(msg.upper()):
        if g not in {"RR73", "RRR"}:
            grid = g
            break
    # Prefer the transmitting station (second call in "W1ABC K4XYZ EL98")
    calls = CALL.findall(msg.upper())
    if msg.upper().startswith("CQ") and calls:
        call = calls[0]
    elif len(calls) >= 2:
        call = calls[1]
    elif calls:
        call = calls[0]
    lat = lon = None
    if grid:
        pos = from_maiden(grid)
        if pos:
            lat, lon = pos
    return {
        "t": ts.timestamp(),
        "mode": mode,
        "freq_hz": freq,
        "snr": int(m.group("score")),
        "callsign": call,
        "grid": grid,
        "lat": lat,
        "lon": lon,
        "msg": msg,
    }


def parse_wspr(line: str, freq_hint_hz: float | None = None) -> dict | None:
    m = WSPR.search(line.strip())
    if not m:
        return None
    now = datetime.now(timezone.utc)
    hhmm = m.group("hhmm")
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    ts = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if ts > now:
        ts = ts - timedelta(days=1)
    freq_mhz = float(m.group("freq"))
    freq_hz = freq_mhz * 1e6 if freq_mhz < 1000 else freq_mhz
    if freq_hint_hz and freq_mhz < 100:
        freq_hz = freq_mhz * 1e6
    grid = m.group("grid").upper()
    pos = from_maiden(grid)
    lat = lon = None
    if pos:
        lat, lon = pos
    return {
        "t": ts.timestamp(),
        "mode": "wspr",
        "freq_hz": freq_hz,
        "snr": int(m.group("snr")),
        "callsign": m.group("call").upper(),
        "grid": grid,
        "lat": lat,
        "lon": lon,
        "msg": line.strip(),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=("ft8", "ft4", "wspr"))
    args = ap.parse_args()
    con = _db()
    for raw in sys.stdin:
        line = raw.rstrip()
        if not line:
            continue
        try:
            if args.mode == "wspr":
                row = parse_wspr(line)
            else:
                row = parse_ftx(line, args.mode)
        except Exception:
            continue
        if row:
            _insert(con, row)
            print(line, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
