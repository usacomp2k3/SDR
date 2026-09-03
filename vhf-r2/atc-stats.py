#!/usr/bin/env python3
"""ATC squelch-open counts → SQLite (durable) + portal JSON snapshot."""
from __future__ import annotations

import calendar
import json
import os
import socket
import sqlite3
import struct
import time

from pathlib import Path as _Path
_HUB = str(_Path(__file__).resolve().parents[1])
GROUP = os.environ.get("ATC_GROUP", "239.245.65.5")
PORT = int(os.environ.get("ATC_PORT", "5004"))
IFACE = os.environ.get("ATC_IFACE", "127.0.0.1")
OUT = os.environ.get("ATC_JSON", str(_HUB) + "/portal/atc-stats.json")
DB = os.environ.get("ATC_DB", str(_HUB) + "/vhf-r2/run/atc.sqlite")
OLD_STATE = os.environ.get(
    "ATC_STATE", str(_HUB) + "/vhf-r2/run/atc-stats.state.json"
)
GAP = float(os.environ.get("ATC_GAP", "1.5"))
JSON_EVERY = float(os.environ.get("ATC_JSON_EVERY", "30"))
KEEP_BURST_S = 30 * 86400  # 30d of per-TX rows; lifetime totals stay in channel

# Match radiod.conf [ATC] raster (25 kHz, inclusive).
RASTER_KHZ = range(128625, 137376, 25)


def mhz_of(ssrc: int) -> float:
    return ssrc / 1000.0


def iso(ts: float | None) -> str | None:
    if not ts:
        return None
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ts))


def _atomic_write(path: str, payload: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(payload)
    os.replace(tmp, path)


def connect() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB) or ".", exist_ok=True)
    cx = sqlite3.connect(DB, timeout=10)
    cx.row_factory = sqlite3.Row
    cx.execute("PRAGMA journal_mode=WAL")
    cx.execute("PRAGMA synchronous=NORMAL")
    cx.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS channel (
          ssrc INTEGER PRIMARY KEY,
          freq_khz INTEGER NOT NULL,
          name TEXT,
          tx INTEGER NOT NULL DEFAULT 0,
          talk_s REAL NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS burst (
          id INTEGER PRIMARY KEY,
          ssrc INTEGER NOT NULL,
          t_start REAL NOT NULL,
          duration_s REAL
        );
        CREATE INDEX IF NOT EXISTS burst_ssrc_t ON burst (ssrc, t_start);
        CREATE INDEX IF NOT EXISTS burst_t ON burst (t_start);
        """
    )
    cols = {r[1] for r in cx.execute("PRAGMA table_info(channel)")}
    if "last_start" in cols:
        cx.execute("ALTER TABLE channel DROP COLUMN last_start")
    for ssrc in RASTER_KHZ:
        cx.execute(
            "INSERT OR IGNORE INTO channel (ssrc, freq_khz, name, tx, talk_s) "
            "VALUES (?, ?, ?, 0, 0)",
            (ssrc, ssrc, f"{mhz_of(ssrc):.3f} MHz"),
        )
    row = cx.execute("SELECT value FROM meta WHERE key = 'started'").fetchone()
    if not row:
        cx.execute(
            "INSERT INTO meta (key, value) VALUES ('started', ?)",
            (str(time.time()),),
        )
    cx.commit()
    return cx


def meta_started(cx: sqlite3.Connection) -> float:
    row = cx.execute("SELECT value FROM meta WHERE key = 'started'").fetchone()
    try:
        return float(row["value"]) if row else time.time()
    except (TypeError, ValueError):
        return time.time()


def migrate_json(cx: sqlite3.Connection) -> None:
    """One-shot: pull lifetime counts from the old JSON state if SQLite is empty."""
    n = cx.execute("SELECT COALESCE(SUM(tx), 0) FROM channel").fetchone()[0]
    if int(n or 0) > 0:
        return
    raw = None
    for path in (OLD_STATE, OUT):
        try:
            with open(path, encoding="utf-8") as f:
                raw = json.load(f)
            break
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            continue
    if not isinstance(raw, dict):
        return
    started = raw.get("started")
    if started is None and raw.get("uptime_s") is not None:
        try:
            started = time.time() - float(raw["uptime_s"])
        except (TypeError, ValueError):
            started = None
    if started:
        cx.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES ('started', ?)",
            (str(float(started)),),
        )
    chans = raw.get("chans")
    if isinstance(chans, dict):
        for key, row in chans.items():
            if not isinstance(row, dict):
                continue
            try:
                ssrc = int(key)
            except (TypeError, ValueError):
                continue
            cx.execute(
                "INSERT INTO channel (ssrc, freq_khz, name, tx, talk_s) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(ssrc) DO UPDATE SET "
                "tx = excluded.tx, talk_s = excluded.talk_s",
                (
                    ssrc,
                    ssrc,
                    f"{mhz_of(ssrc):.3f} MHz",
                    int(row.get("tx") or 0),
                    float(row.get("talk_s") or 0),
                ),
            )
            for t in row.get("bursts") or []:
                try:
                    ts = float(t)
                except (TypeError, ValueError):
                    continue
                cx.execute(
                    "INSERT INTO burst (ssrc, t_start, duration_s) VALUES (?, ?, NULL)",
                    (ssrc, ts),
                )
    elif isinstance(raw.get("channels"), list):
        for row in raw["channels"]:
            if not isinstance(row, dict):
                continue
            try:
                ssrc = int(row.get("ssrc") or 0)
            except (TypeError, ValueError):
                continue
            if not ssrc:
                continue
            last = row.get("last")
            last_ts = None
            if last:
                try:
                    last_ts = calendar.timegm(
                        time.strptime(last, "%Y-%m-%dT%H:%M:%SZ")
                    )
                except (TypeError, ValueError, OverflowError):
                    last_ts = None
            cx.execute(
                "INSERT INTO channel (ssrc, freq_khz, name, tx, talk_s) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(ssrc) DO UPDATE SET "
                "tx = excluded.tx, talk_s = excluded.talk_s",
                (
                    ssrc,
                    int(row.get("freq_mhz", 0) * 1000) or ssrc,
                    row.get("name") or f"{mhz_of(ssrc):.3f} MHz",
                    int(row.get("tx") or 0),
                    float(row.get("talk_s") or 0),
                ),
            )
            if last_ts:
                cx.execute(
                    "INSERT INTO burst (ssrc, t_start, duration_s) VALUES (?, ?, NULL)",
                    (ssrc, last_ts),
                )
    cx.commit()
    print(f"atc-stats migrated previous JSON into {DB}", flush=True)


def snapshot(cx: sqlite3.Connection, now: float) -> dict:
    started = meta_started(cx)
    cutoff = now - 3600
    rows = []
    for c in cx.execute(
        "SELECT c.ssrc, c.freq_khz, c.name, c.tx, c.talk_s, "
        "  (SELECT COUNT(*) FROM burst b WHERE b.ssrc = c.ssrc AND b.t_start >= ?) AS tx_1h, "
        "  (SELECT MAX(b.t_start) FROM burst b WHERE b.ssrc = c.ssrc) AS last_start "
        "FROM channel c ORDER BY c.tx DESC, c.freq_khz ASC",
        (cutoff,),
    ):
        mhz = (c["freq_khz"] or c["ssrc"]) / 1000.0
        if not (128.5 <= mhz <= 137.5):
            continue
        rows.append(
            {
                "ssrc": c["ssrc"],
                "freq_mhz": round(mhz, 3),
                "name": c["name"] or f"{mhz:.3f} MHz",
                "tx": int(c["tx"] or 0),
                "tx_1h": int(c["tx_1h"] or 0),
                "talk_s": round(float(c["talk_s"] or 0), 1),
                "last": iso(c["last_start"]),
            }
        )
    return {
        "updated": iso(now),
        "uptime_s": round(now - started, 1),
        "since": iso(started),
        "db": DB,
        "note": "25 kHz raster 128.625–137.375 (except ACARS/VDL2). Snapshot 30s; SQLite survives restarts.",
        "channels": rows,
        "tx_total": sum(r["tx"] for r in rows),
        "tx_1h": sum(r["tx_1h"] for r in rows),
    }


def write_json(cx: sqlite3.Connection, now: float) -> None:
    _atomic_write(OUT, json.dumps(snapshot(cx, now), separators=(",", ":")))


class Open:
    __slots__ = ("ssrc", "t0", "last", "rowid")

    def __init__(self, ssrc: int, t0: float, rowid: int):
        self.ssrc = ssrc
        self.t0 = t0
        self.last = t0
        self.rowid = rowid


def main() -> None:
    cx = connect()
    migrate_json(cx)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
    except OSError:
        pass
    try:
        sock.setsockopt(socket.IPPROTO_IP, 49, 0)  # IP_MULTICAST_ALL
    except OSError:
        pass
    sock.bind((GROUP, PORT))
    mreq = struct.pack("4s4s", socket.inet_aton(GROUP), socket.inet_aton(IFACE))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    sock.settimeout(0.25)

    open_ch: dict[int, Open] = {}
    last_json = 0.0
    last_prune = 0.0

    def close_idle(now: float) -> None:
        done = []
        for ssrc, o in open_ch.items():
            if now - o.last < GAP:
                continue
            dur = max(0.0, o.last - o.t0)
            cx.execute(
                "UPDATE burst SET duration_s = ? WHERE id = ?",
                (round(dur, 3), o.rowid),
            )
            cx.execute(
                "UPDATE channel SET talk_s = talk_s + ? WHERE ssrc = ?",
                (dur, ssrc),
            )
            done.append(ssrc)
        for ssrc in done:
            del open_ch[ssrc]
        if done:
            cx.commit()

    def prune(now: float) -> None:
        # Keep the newest burst per channel so last-heard survives the 30d trim.
        cx.execute(
            "DELETE FROM burst WHERE t_start < ? AND id NOT IN ("
            "  SELECT id FROM ("
            "    SELECT b.id FROM burst b"
            "    INNER JOIN (SELECT ssrc, MAX(t_start) AS t FROM burst GROUP BY ssrc) m"
            "      ON m.ssrc = b.ssrc AND m.t = b.t_start"
            "  )"
            ")",
            (now - KEEP_BURST_S,),
        )
        cx.commit()

    def open_tx(ssrc: int, now: float) -> None:
        cx.execute(
            "INSERT INTO channel (ssrc, freq_khz, name, tx, talk_s) "
            "VALUES (?, ?, ?, 1, 0) "
            "ON CONFLICT(ssrc) DO UPDATE SET tx = tx + 1",
            (ssrc, ssrc, f"{mhz_of(ssrc):.3f} MHz"),
        )
        cur = cx.execute(
            "INSERT INTO burst (ssrc, t_start, duration_s) VALUES (?, ?, NULL)",
            (ssrc, now),
        )
        cx.commit()
        open_ch[ssrc] = Open(ssrc, now, int(cur.lastrowid))

    print(f"atc-stats sqlite {DB} json {OUT} join {GROUP}:{PORT}", flush=True)
    write_json(cx, time.time())

    while True:
        now = time.time()
        try:
            data, _ = sock.recvfrom(2048)
        except socket.timeout:
            close_idle(now)
            if now - last_json >= JSON_EVERY:
                write_json(cx, now)
                last_json = now
            if now - last_prune >= 3600:
                prune(now)
                last_prune = now
            continue
        if len(data) < 12:
            continue
        ssrc = struct.unpack("!I", data[8:12])[0]
        mhz = mhz_of(ssrc)
        if not (128.5 <= mhz <= 137.5):
            continue
        o = open_ch.get(ssrc)
        if o is None or now - o.last >= GAP:
            if o is not None:
                close_idle(now)
            open_tx(ssrc, now)
            write_json(cx, now)
            last_json = now
        else:
            o.last = now


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
