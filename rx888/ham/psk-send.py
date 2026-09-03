#!/usr/bin/env python3
"""Tail a decode log and upload to PSK Reporter."""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

_HUB = str(Path(__file__).resolve().parents[2])
sys.path.insert(0, str(_HUB) + "/rx888/src/ftlib-pskreporter")
sys.path.insert(0, str(_HUB) + "/rx888/ham")
from pskreporter import PskReporter  # noqa: E402

from ingest import parse_ftx, parse_wspr  # noqa: E402


def main() -> int:
    if len(sys.argv) < 3:
        print("usage: psk-send.py <logfile> <ft8|ft4|wspr>", file=sys.stderr)
        return 2
    logfile, mode = sys.argv[1], sys.argv[2].lower()
    Path(logfile).touch(exist_ok=True)
    psk = PskReporter(
        callsign=os.environ.get("HF_CALLSIGN", "WP-KMCO").upper(),
        grid=os.environ.get("HF_GRID", "EL98ho"),
        antenna=os.environ.get("HF_ANTENNA", "MLA30+"),
        software=os.environ.get("HF_SOFTWARE", "WP-KMCO ka9q-radio"),
        dummy=False,
        tcp=True,
    )
    proc = subprocess.Popen(
        ["tail", "-n", "0", "-F", logfile], stdout=subprocess.PIPE, text=True
    )
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            if mode == "wspr":
                row = parse_wspr(line)
            else:
                row = parse_ftx(line, mode)
            if not row or not row.get("callsign"):
                continue
            psk.spot(
                callsign=row["callsign"],
                frequency=float(row["freq_hz"] or 0),
                mode=mode.upper(),
                timestamp=int(row["t"] or time.time()),
                db=row.get("snr"),
                locator=row.get("grid"),
            )
    except KeyboardInterrupt:
        pass
    finally:
        try:
            psk.close()
        except Exception:
            pass
        proc.terminate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
