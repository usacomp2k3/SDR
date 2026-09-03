#!/usr/bin/env python3
"""
WP-KMCO unified coverage map API.

Collects lat/lon points only when the map requests them (no always-on recorder).
Live snapshots (ADS-B / UAT / AIS / mobile) are merged into a small on-disk history
so reopening the map over time builds a richer picture. ACARS-family positions
and radiosonde tracks come from existing station stores.

  GET /api/map/points?window=24h&sources=all
  GET /api/map/meta
  GET /health
"""
from __future__ import annotations

import csv
import json
import math
import os
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

LISTEN = ("0.0.0.0", 8882)
STATION_LAT = float(os.environ.get("MAP_STATION_LAT", "28.4294"))
STATION_LON = float(os.environ.get("MAP_STATION_LON", "-81.3089"))
STATION_NAME = os.environ.get("MAP_STATION_NAME", "WP-KMCO / WinterPark")

ACARS_DB = Path(os.environ.get("MAP_ACARS_DB", "/data/acars/messages.db"))
SONDE_LOG_DIR = Path(os.environ.get("MAP_SONDE_LOG", "/data/sonde"))
HISTORY_DB = Path(os.environ.get("MAP_HISTORY_DB", "/data/map/history.db"))
HAM_DB = Path(os.environ.get("MAP_HAM_DB", "/data/ham/spots.db"))
IRDM_POS = Path(os.environ.get("MAP_IRDM_POS", "/data/irdm/irdm-pos.jsonl"))

HTTP_TIMEOUT = float(os.environ.get("MAP_HTTP_TIMEOUT", "3.5"))
MOBILE_HOST = os.environ.get("MAP_MOBILE_HOST", "").strip()
MOBILE_ADSB_URL = os.environ.get("MAP_MOBILE_ADSB_URL", "").strip()
MOBILE_UAT_URL = os.environ.get("MAP_MOBILE_UAT_URL", "").strip()
if MOBILE_HOST and not MOBILE_ADSB_URL:
    MOBILE_ADSB_URL = f"http://{MOBILE_HOST}:8080/data/aircraft.json"
if MOBILE_HOST and not MOBILE_UAT_URL:
    MOBILE_UAT_URL = f"http://{MOBILE_HOST}:9780/skyaware978/data/aircraft.json"

# Five high-contrast colors for 1090 / 978 / VHF / VDL2 / HFDL (color-blind safe).
# Shape-coded layers (icons) use neutral fills — identity is the glyph, not the hue.
SOURCES: dict[str, dict[str, Any]] = {
    "adsb-1090": {
        "label": "ADS-B 1090",
        "color": "#FF5A00",  # vivid orange
        "default_on": True,
        "kind": "live",
        "dense": True,
        "icon": "dot",
    },
    "uat-978": {
        "label": "UAT 978",
        "color": "#00C2FF",  # bright cyan / light blue
        "default_on": True,
        "kind": "live",
        "dense": False,
        "icon": "dot",
    },
    "adsc": {
        "label": "ADS-C (all)",
        "color": "#9EFA9E",  # tar1090 adsc green
        "default_on": False,
        "kind": "live",
        "dense": False,
        "icon": "dot",
        "note": "Combined overlay — same points already sit on VHF / VDL2 / HFDL / Iridium",
    },
    "adsb-mobile": {
        "label": "Mobile minivan 1090/978",
        "color": "#8B1515",  # dark red (matches the van)
        "default_on": False,
        "kind": "live",
        "dense": True,
        "icon": "minivan",
        "note": f"adsb.im mobile @ {MOBILE_HOST}",
    },
    "acars-vhf": {
        "label": "ACARS VHF",
        "color": "#FF1A4B",  # punchy red
        "default_on": True,
        "kind": "history",
        "message_types": ["ACARS"],
        "icon": "dot",
        "note": "Includes VHF ADS-C / POS",
    },
    "acars-vdl2": {
        "label": "VDL2",
        "color": "#F5F5F5",  # near-white (dark stroke on map)
        "default_on": True,
        "kind": "history",
        "message_types": ["VDL-M2", "VDLM2", "VDL2"],
        "icon": "dot",
        "note": "Includes VDL ADS-C / POS",
    },
    "acars-hfdl": {
        "label": "HFDL",
        "color": "#1B4DFF",  # strong blue
        "default_on": True,
        "kind": "history",
        "message_types": ["HFDL"],
        "icon": "dot",
        "note": "Includes HFDL ADS-C",
    },
    "acars-iridium": {
        "label": "Iridium ACARS",
        "color": "#D4D4D8",
        "default_on": True,
        "kind": "history",
        "message_types": ["IRDM", "IMSL"],
        "icon": "satellite",
        "note": "Includes Iridium ADS-C",
    },
    "ais-162": {
        "label": "AIS 162",
        "color": "#E4E4E7",
        "default_on": True,
        "kind": "live",
        "dense": False,
        "icon": "boat",
    },
    "radiosonde-400": {
        "label": "Radiosonde 400",
        "color": "#FAFAFA",
        "default_on": True,
        "kind": "history",
        "icon": "parachute",
    },
    "ham-ft8": {
        "label": "FT8",
        "color": "#A3E635",
        "default_on": True,
        "kind": "ham",
        "ham_mode": "ft8",
        "icon": "dot",
    },
    "ham-ft4": {
        "label": "FT4",
        "color": "#34D399",
        "default_on": True,
        "kind": "ham",
        "ham_mode": "ft4",
        "icon": "dot",
    },
    "ham-wspr": {
        "label": "WSPR",
        "color": "#FBBF24",
        "default_on": True,
        "kind": "ham",
        "ham_mode": "wspr",
        "icon": "dot",
    },
}

WINDOWS = {
    "1h": 3600,
    "8h": 8 * 3600,
    "24h": 24 * 3600,
    "7d": 7 * 24 * 3600,
    "all": None,
}

# Live-source re-sample: ignore identical id within this many seconds when storing
DEDUPE_SECONDS = 45
# Cap points returned per sparse source / per dense point mode
MAX_POINTS_SPARSE = 4000
MAX_POINTS_DENSE = 2500
# Radiosonde logs are ~1 Hz; thin like ADS-B "one ping stream → sparse track"
MAX_SONDE_PER_TRACK = 48
SONDE_MIN_INTERVAL_SEC = 120.0  # keep at most ~1 point / 2 min unless it moved
SONDE_MIN_DIST_KM = 2.5  # or when it has traveled this far from last kept point
HEAT_BIN_DEG = 0.03  # ~3 km
MAX_HEAT_CELLS = 8000
# Tracks are built ONLY from our map-history / sonde logs (never feeder trace APIs)
TRACK_SOURCE_IDS = frozenset(
    {"adsb-1090", "uat-978", "adsb-mobile", "adsc", "radiosonde-400"}
)
MAX_OWN_TRACKS = 800
PROTOCOL_SOURCES = ("acars-vhf", "acars-vdl2", "acars-hfdl", "acars-iridium")
# acars2pos stamps exclusive datalink tracks: 1=VHF ACARS, 3/4=VDL, 5/6=HFDL.
# Anything else (blank / 0 / 7+) is treated as Iridium — not a 1090 squawk.
_SQUAWK_PROTOCOL = {
    "1": "acars-vhf",
    "3": "acars-vdl2",
    "4": "acars-vdl2",
    "5": "acars-hfdl",
    "6": "acars-hfdl",
}

# N 29.551,W 82.577  /  N29.551 W82.577
_RE_DEC = re.compile(
    r"\b([NS])\s*(\d{1,2}(?:\.\d{1,5})?)\s*[, ]\s*([EW])\s*(\d{1,3}(?:\.\d{1,5})?)\b",
    re.I,
)
# POSN 30.620W 81.191
_RE_POSN = re.compile(
    r"POS\s*([NS])\s*(\d{1,2}(?:\.\d{1,5})?)\s*([EW])\s*(\d{1,3}(?:\.\d{1,5})?)",
    re.I,
)
# Compact N30360W081430 (deg + tenths of minutes) glued into WOB frames
_RE_COMPACT = re.compile(r"([NS])(\d{2})(\d{3})([EW])(\d{3})(\d{3})", re.I)


def _now() -> float:
    return time.time()


def _http_json(url: str, timeout: float = HTTP_TIMEOUT) -> Any | None:
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "wp-kmco-map-api/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError, ValueError):
        return None


def _valid_coord(lat: Any, lon: Any) -> tuple[float, float] | None:
    try:
        la = float(lat)
        lo = float(lon)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(la) or not math.isfinite(lo):
        return None
    if la == 0.0 and lo == 0.0:
        return None
    if abs(la) > 90 or abs(lo) > 180:
        return None
    return la, lo


def _hem_coord(hem: str, deg: float, lon: bool = False) -> float | None:
    limit = 180.0 if lon else 90.0
    if deg < 0 or deg > limit:
        return None
    hem = hem.upper()
    if hem in ("S", "W"):
        deg = -deg
    elif hem not in ("N", "E"):
        return None
    return deg


def parse_acars_position(text: str) -> tuple[float, float] | None:
    """Pull a lat/lon out of common ACARS POS / WOB / decimal-degree payloads."""
    if not text:
        return None
    m = _RE_POSN.search(text) or _RE_DEC.search(text)
    if m:
        lat = _hem_coord(m.group(1), float(m.group(2)))
        lon = _hem_coord(m.group(3), float(m.group(4)), lon=True)
        if lat is not None and lon is not None and not (lat == 0.0 and lon == 0.0):
            return lat, lon
    if "WOB" in text.upper() or "*WO" in text.upper() or "POS" in text.upper():
        m = _RE_COMPACT.search(text)
        if m:
            lat = _hem_coord(m.group(1), int(m.group(2)) + int(m.group(3)) / 600.0)
            lon = _hem_coord(m.group(4), int(m.group(5)) + int(m.group(6)) / 600.0, lon=True)
            if lat is not None and lon is not None:
                return lat, lon
    return None


def _walk_latlon(obj: Any) -> tuple[float, float] | None:
    if isinstance(obj, dict):
        if "lat" in obj and "lon" in obj:
            found = _valid_coord(obj.get("lat"), obj.get("lon"))
            if found:
                return found
        for v in obj.values():
            found = _walk_latlon(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_latlon(v)
            if found:
                return found
    return None


def _libacars_pos(raw: Any) -> tuple[float, float] | None:
    if not raw:
        return None
    if isinstance(raw, (bytes, bytearray)):
        raw = raw.decode("utf-8", errors="replace")
    if isinstance(raw, str):
        s = raw.strip()
        if not s.startswith("{") and not s.startswith("["):
            return parse_acars_position(s)
        try:
            raw = json.loads(s)
        except json.JSONDecodeError:
            return parse_acars_position(s)
    return _walk_latlon(raw)


def protocol_from_squawk(squawk: Any) -> str:
    s = str(squawk or "").strip()
    if s and s[0] in _SQUAWK_PROTOCOL:
        return _SQUAWK_PROTOCOL[s[0]]
    return "acars-iridium"


def collect_adsc_split() -> tuple[dict[str, list[dict[str, Any]]], dict[str, Any]]:
    """Live tar1090 type=adsc, bucketed by acars2pos squawk → protocol layer."""
    buckets: dict[str, list[dict[str, Any]]] = {s: [] for s in PROTOCOL_SOURCES}
    buckets["_all"] = []
    url = "http://127.0.0.1:8085/data/aircraft.json"
    meta: dict[str, Any] = {"url": url, "ok": False}
    data = _http_json(url)
    if not data or not isinstance(data, dict):
        meta["error"] = "unreachable"
        return buckets, meta
    aircraft = data.get("aircraft") or []
    now = float(data.get("now") or _now())
    if now < 1e9:
        now = _now()
    for a in aircraft:
        if not isinstance(a, dict):
            continue
        if str(a.get("type") or "") != "adsc":
            continue
        coord = _valid_coord(a.get("lat"), a.get("lon"))
        if not coord:
            continue
        la, lo = coord
        hx = str(a.get("hex") or "").strip().lower()
        flight = str(a.get("flight") or a.get("r") or hx or "").strip()
        seen = a.get("seen_pos")
        t = now
        if isinstance(seen, (int, float)):
            t = now - float(seen)
        pt = {
            "lat": la,
            "lon": lo,
            "t": t,
            "id": f"c-{hx}" if hx else None,
            "label": flight or hx,
        }
        proto = protocol_from_squawk(a.get("squawk"))
        buckets[proto].append(pt)
        buckets["_all"].append(pt)
    meta["ok"] = True
    meta["live_count"] = len(buckets["_all"])
    meta["by_protocol"] = {s: len(buckets[s]) for s in PROTOCOL_SOURCES}
    return buckets, meta


def collect_irdm_sidecar(
    t_min: float | None, t_max: float
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"path": str(IRDM_POS), "ok": False}
    if not IRDM_POS.exists():
        meta["error"] = "missing"
        return [], meta
    pts: list[dict[str, Any]] = []
    try:
        with IRDM_POS.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line.startswith("{"):
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(rec, dict):
                    continue
                coord = _valid_coord(rec.get("lat"), rec.get("lon"))
                if not coord:
                    continue
                t = _parse_ts(rec.get("t")) or 0.0
                if t > t_max:
                    continue
                if t_min is not None and t < t_min:
                    continue
                la, lo = coord
                label = str(rec.get("label") or rec.get("id") or "").strip()
                pts.append(
                    {
                        "lat": la,
                        "lon": lo,
                        "t": t,
                        "id": str(rec.get("id") or label or "").lower() or None,
                        "label": label,
                    }
                )
    except OSError as e:
        meta["error"] = str(e)
        return [], meta
    meta["ok"] = True
    meta["raw_count"] = len(pts)
    return pts, meta


def _parse_ts(val: Any) -> float | None:
    if val is None or val == "":
        return None
    if isinstance(val, (int, float)):
        t = float(val)
        # ms vs s
        if t > 1e12:
            t /= 1000.0
        return t if t > 1e9 else None
    s = str(val).strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        return None


# ── history store (only written on map collect) ─────────────────────────────

def _hist_conn() -> sqlite3.Connection:
    HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(HISTORY_DB), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS points (
            source TEXT NOT NULL,
            t REAL NOT NULL,
            lat REAL NOT NULL,
            lon REAL NOT NULL,
            id TEXT,
            label TEXT,
            PRIMARY KEY (source, id, t)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_points_src_t ON points(source, t)"
    )
    return conn


def _store_points(
    source: str,
    pts: list[dict[str, Any]],
    *,
    dedupe_sec: float | None = None,
) -> int:
    if not pts:
        return 0
    if dedupe_sec is None:
        dedupe_sec = float(DEDUPE_SECONDS)
    conn = _hist_conn()
    now = _now()
    stored = 0
    try:
        for p in pts:
            pid = str(p.get("id") or "")
            t = float(p.get("t") or now)
            # Skip if we already have this id very recently (disabled when dedupe_sec<=0)
            if pid and dedupe_sec > 0:
                row = conn.execute(
                    "SELECT t FROM points WHERE source=? AND id=? ORDER BY t DESC LIMIT 1",
                    (source, pid),
                ).fetchone()
                if row and (t - float(row[0])) < dedupe_sec:
                    continue
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO points(source, t, lat, lon, id, label) VALUES (?,?,?,?,?,?)",
                    (
                        source,
                        t,
                        float(p["lat"]),
                        float(p["lon"]),
                        pid or None,
                        p.get("label"),
                    ),
                )
                stored += 1
            except sqlite3.Error:
                continue
        # Retention: keep ~14 days for live sources so "all" stays useful
        cutoff = now - 14 * 24 * 3600
        conn.execute("DELETE FROM points WHERE t < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()
    return stored


def _load_history(source: str, t_min: float | None, t_max: float) -> list[dict[str, Any]]:
    if not HISTORY_DB.exists():
        return []
    conn = _hist_conn()
    try:
        if t_min is None:
            rows = conn.execute(
                "SELECT t, lat, lon, id, label FROM points WHERE source=? AND t<=? ORDER BY t",
                (source, t_max),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT t, lat, lon, id, label FROM points WHERE source=? AND t>=? AND t<=? ORDER BY t",
                (source, t_min, t_max),
            ).fetchall()
    finally:
        conn.close()
    return [
        {
            "lat": r[1],
            "lon": r[2],
            "t": r[0],
            "id": r[3],
            "label": r[4],
        }
        for r in rows
    ]


# ── live collectors ─────────────────────────────────────────────────────────

def collect_aircraft_json(
    url: str,
    id_prefix: str = "",
    *,
    types: frozenset[str] | None = None,
    exclude_types: frozenset[str] | None = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"url": url, "ok": False}
    if not url:
        meta["error"] = "unset"
        return [], meta
    data = _http_json(url)
    if not data or not isinstance(data, dict):
        meta["error"] = "unreachable"
        return [], meta
    aircraft = data.get("aircraft") or []
    now = float(data.get("now") or _now())
    # readsb "now" is sometimes a large epoch; trust if plausible
    if now < 1e9:
        now = _now()
    pts: list[dict[str, Any]] = []
    for a in aircraft:
        if not isinstance(a, dict):
            continue
        typ = str(a.get("type") or "")
        if types is not None and typ not in types:
            continue
        if exclude_types is not None and typ in exclude_types:
            continue
        coord = _valid_coord(a.get("lat"), a.get("lon"))
        if not coord:
            continue
        la, lo = coord
        hx = str(a.get("hex") or "").strip().lower()
        flight = str(a.get("flight") or a.get("r") or hx or "").strip()
        seen = a.get("seen_pos")
        t = now
        if isinstance(seen, (int, float)):
            t = now - float(seen)
        pts.append(
            {
                "lat": la,
                "lon": lo,
                "t": t,
                "id": f"{id_prefix}{hx}" if hx else None,
                "label": flight or hx,
            }
        )
    meta["ok"] = True
    meta["live_count"] = len(pts)
    return pts, meta


def collect_ais() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    url = "http://127.0.0.1:8090/ships.json"
    meta: dict[str, Any] = {"url": url, "ok": False}
    data = _http_json(url)
    if not data or not isinstance(data, dict):
        meta["error"] = "unreachable"
        return [], meta
    now = _now()
    pts: list[dict[str, Any]] = []
    for s in data.get("ships") or []:
        if not isinstance(s, dict):
            continue
        coord = _valid_coord(s.get("lat"), s.get("lon"))
        if not coord:
            continue
        la, lo = coord
        mmsi = str(s.get("mmsi") or "")
        name = str(s.get("shipname") or mmsi).strip()
        last = s.get("last_signal")
        t = now
        if isinstance(last, (int, float)):
            t = now - float(last)
        pts.append(
            {
                "lat": la,
                "lon": lo,
                "t": t,
                "id": mmsi or None,
                "label": name or mmsi,
            }
        )
    meta["ok"] = True
    meta["live_count"] = len(pts)
    return pts, meta


def collect_acars(
    source: str,
    t_min: float | None,
    t_max: float,
    *,
    thin: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"path": str(ACARS_DB), "ok": False}
    types = SOURCES[source].get("message_types") or []
    if not ACARS_DB.exists():
        meta["error"] = "db_missing"
        return [], meta
    try:
        # read-only
        uri = f"file:{ACARS_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=8)
    except sqlite3.Error as e:
        meta["error"] = str(e)
        return [], meta
    try:
        placeholders = ",".join("?" for _ in types)
        params: list[Any] = list(types)
        where = [
            f"message_type IN ({placeholders})",
            "msg_time <= ?",
        ]
        params.append(int(t_max))
        if t_min is not None:
            where.append("msg_time >= ?")
            params.append(int(t_min))
        sql = (
            f"SELECT msg_time, lat, lon, tail, flight, icao, message_type, "
            f"msg_text, libacars "
            f"FROM messages WHERE {' AND '.join(where)} ORDER BY msg_time"
        )
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.Error as e:
        meta["error"] = str(e)
        return [], meta
    finally:
        conn.close()

    pts: list[dict[str, Any]] = []
    parsed_from_text = 0
    for msg_time, lat, lon, tail, flight, icao, _mt, msg_text, libacars in rows:
        coord = _valid_coord(lat, lon)
        if not coord:
            coord = _libacars_pos(libacars)
        if not coord:
            coord = parse_acars_position(str(msg_text or ""))
            if coord:
                parsed_from_text += 1
        if not coord:
            continue
        la, lo = coord
        label = (flight or tail or icao or "").strip()
        pid = (icao or tail or flight or "").strip().lower() or None
        pts.append(
            {
                "lat": la,
                "lon": lo,
                "t": float(msg_time),
                "id": pid,
                "label": label or pid,
            }
        )
    meta["parsed_from_text"] = parsed_from_text
    meta["ok"] = True
    meta["raw_count"] = len(pts)
    meta["thin"] = {"enabled": bool(thin)}
    if thin:
        return _downsample(pts, MAX_POINTS_SPARSE), meta
    return pts, meta


def collect_ham(
    source: str,
    t_min: float | None,
    t_max: float,
    *,
    thin: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    mode = SOURCES[source].get("ham_mode") or "ft8"
    meta: dict[str, Any] = {"path": str(HAM_DB), "ok": False, "mode": mode}
    if not HAM_DB.exists():
        meta["error"] = "db_missing"
        return [], meta
    try:
        uri = f"file:{HAM_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=8)
    except sqlite3.Error as e:
        meta["error"] = str(e)
        return [], meta
    try:
        where = ["mode=?", "lat IS NOT NULL", "lon IS NOT NULL", "t<=?"]
        params: list[Any] = [mode, t_max]
        if t_min is not None:
            where.append("t>=?")
            params.append(t_min)
        rows = conn.execute(
            f"SELECT t, lat, lon, callsign, grid, freq_hz, snr, msg FROM spots "
            f"WHERE {' AND '.join(where)} ORDER BY t",
            params,
        ).fetchall()
    except sqlite3.Error as e:
        meta["error"] = str(e)
        return [], meta
    finally:
        conn.close()
    pts: list[dict[str, Any]] = []
    for t, lat, lon, call, grid, freq, snr, msg in rows:
        coord = _valid_coord(lat, lon)
        if not coord:
            continue
        la, lo = coord
        label = f"{call or '?'} {grid or ''}".strip()
        pts.append(
            {
                "lat": la,
                "lon": lo,
                "t": float(t),
                "id": (call or grid or "").lower() or None,
                "label": label,
                "freq": freq,
                "snr": snr,
                "msg": msg,
            }
        )
    meta["ok"] = True
    meta["raw_count"] = len(pts)
    if thin:
        return _downsample(pts, MAX_POINTS_SPARSE), meta
    return pts, meta


def ham_summary(limit: int = 80) -> dict[str, Any]:
    out: dict[str, Any] = {"ok": False, "modes": {}, "recent": []}
    if not HAM_DB.exists():
        out["error"] = "db_missing"
        return out
    try:
        uri = f"file:{HAM_DB}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=8)
    except sqlite3.Error as e:
        out["error"] = str(e)
        return out
    now = _now()
    try:
        for mode in ("ft8", "ft4", "wspr"):
            n1 = conn.execute(
                "SELECT count(*) FROM spots WHERE mode=? AND t>?", (mode, now - 3600)
            ).fetchone()[0]
            n24 = conn.execute(
                "SELECT count(*) FROM spots WHERE mode=? AND t>?", (mode, now - 86400)
            ).fetchone()[0]
            last = conn.execute(
                "SELECT t FROM spots WHERE mode=? ORDER BY t DESC LIMIT 1", (mode,)
            ).fetchone()
            out["modes"][mode] = {
                "last_hour": n1,
                "last_24h": n24,
                "last_t": last[0] if last else None,
            }
        rows = conn.execute(
            "SELECT t, mode, freq_hz, snr, callsign, grid, lat, lon, msg "
            "FROM spots ORDER BY t DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out["recent"] = [
            {
                "t": r[0],
                "mode": r[1],
                "freq_hz": r[2],
                "snr": r[3],
                "callsign": r[4],
                "grid": r[5],
                "lat": r[6],
                "lon": r[7],
                "msg": r[8],
            }
            for r in rows
        ]
        out["ok"] = True
    except sqlite3.Error as e:
        out["error"] = str(e)
    finally:
        conn.close()
    return out


def collect_sondes(
    t_min: float | None,
    t_max: float,
    *,
    thin: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    meta: dict[str, Any] = {"path": str(SONDE_LOG_DIR), "ok": False}
    if not SONDE_LOG_DIR.exists():
        meta["error"] = "log_dir_missing"
        return [], meta

    pts: list[dict[str, Any]] = []
    files_used = 0
    raw_total = 0
    for path in sorted(SONDE_LOG_DIR.glob("*_sonde.log")):
        # Quick filter by mtime
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if t_min is not None and mtime < t_min - 6 * 3600:
            # log may have ended earlier; still allow recent files only when far outside window
            if mtime < (t_min or 0) - 24 * 3600:
                continue
        track: list[dict[str, Any]] = []
        try:
            with path.open("r", encoding="utf-8", errors="replace") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    ts = _parse_ts(row.get("timestamp") or row.get("datetime"))
                    if ts is None:
                        continue
                    if t_min is not None and ts < t_min:
                        continue
                    if ts > t_max:
                        continue
                    coord = _valid_coord(row.get("lat"), row.get("lon"))
                    if not coord:
                        continue
                    la, lo = coord
                    serial = str(row.get("serial") or path.stem).strip()
                    track.append(
                        {
                            "lat": la,
                            "lon": lo,
                            "t": ts,
                            "id": serial,
                            "label": serial,
                        }
                    )
        except OSError:
            continue
        if track:
            files_used += 1
            raw_total += len(track)
            # 1h window: keep every fix; longer windows thin for readability
            if thin:
                pts.extend(_thin_sonde_track(track))
            else:
                pts.extend(sorted(track, key=lambda p: float(p.get("t") or 0)))

    meta["ok"] = True
    meta["files"] = files_used
    meta["raw_count"] = raw_total
    meta["thinned_count"] = len(pts)
    meta["thin"] = (
        {
            "enabled": True,
            "min_interval_sec": SONDE_MIN_INTERVAL_SEC,
            "min_dist_km": SONDE_MIN_DIST_KM,
            "max_per_track": MAX_SONDE_PER_TRACK,
        }
        if thin
        else {"enabled": False, "reason": "full resolution for short window"}
    )
    return pts, meta


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _thin_sonde_track(track: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Collapse a high-rate sonde log into a readable path:
    always keep first + last; keep intermediate only when enough time OR distance
    has passed since the last kept fix (same idea as ADS-B not plotting every ping).
    """
    if len(track) <= 2:
        return track
    track = sorted(track, key=lambda p: float(p.get("t") or 0))
    kept: list[dict[str, Any]] = [track[0]]
    last = track[0]
    for p in track[1:-1]:
        dt = float(p.get("t") or 0) - float(last.get("t") or 0)
        dist = _haversine_km(
            float(last["lat"]), float(last["lon"]), float(p["lat"]), float(p["lon"])
        )
        if dt >= SONDE_MIN_INTERVAL_SEC or dist >= SONDE_MIN_DIST_KM:
            kept.append(p)
            last = p
    # Always include final fix (landing / last heard)
    final = track[-1]
    if kept[-1] is not final:
        # Drop near-duplicate of last if we just kept something almost identical
        dist_f = _haversine_km(
            float(kept[-1]["lat"]),
            float(kept[-1]["lon"]),
            float(final["lat"]),
            float(final["lon"]),
        )
        dt_f = float(final.get("t") or 0) - float(kept[-1].get("t") or 0)
        if dist_f >= 0.15 or dt_f >= 30:
            kept.append(final)
        else:
            kept[-1] = final
    if len(kept) > MAX_SONDE_PER_TRACK:
        kept = _downsample(kept, MAX_SONDE_PER_TRACK)
    return kept


def _downsample(pts: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    if len(pts) <= limit:
        return pts
    # Evenly spaced sample, always keep first/last
    n = len(pts)
    idxs = {0, n - 1}
    for i in range(limit - 2):
        idxs.add(1 + int(i * (n - 2) / max(limit - 2, 1)))
    return [pts[i] for i in sorted(idxs)]


def _merge_unique(live: list[dict[str, Any]], hist: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    """Prefer history spread + freshest live; dedupe by id+rounded time bucket."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for p in hist + live:
        key = (
            round(float(p["lat"]), 4),
            round(float(p["lon"]), 4),
            int(float(p.get("t") or 0) // 30),
            p.get("id"),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    if limit > 0 and len(out) > limit:
        out = _downsample(sorted(out, key=lambda x: float(x.get("t") or 0)), limit)
    return out


def _concat_all_points(
    live: list[dict[str, Any]], hist: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """No thinning: every history + live sample (exact lat/lon/t/id only once)."""
    seen: set[tuple[Any, ...]] = set()
    out: list[dict[str, Any]] = []
    for p in list(hist) + list(live):
        key = (
            p.get("id"),
            float(p.get("t") or 0),
            round(float(p["lat"]), 6),
            round(float(p["lon"]), 6),
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(p)
    out.sort(key=lambda x: float(x.get("t") or 0))
    return out


def _heat_bins(pts: list[dict[str, Any]]) -> list[list[float]]:
    """Return [[lat, lon, weight], ...] cell centers."""
    if not pts:
        return []
    bins: dict[tuple[int, int], int] = {}
    for p in pts:
        i = int(math.floor(float(p["lat"]) / HEAT_BIN_DEG))
        j = int(math.floor(float(p["lon"]) / HEAT_BIN_DEG))
        bins[(i, j)] = bins.get((i, j), 0) + 1
    cells = [
        [
            (i + 0.5) * HEAT_BIN_DEG,
            (j + 0.5) * HEAT_BIN_DEG,
            float(w),
        ]
        for (i, j), w in bins.items()
    ]
    cells.sort(key=lambda c: c[2], reverse=True)
    return cells[:MAX_HEAT_CELLS]


def _latest_unique(pts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One point per id (freshest) — good for live aircraft icons."""
    best: dict[str, dict[str, Any]] = {}
    orphans: list[dict[str, Any]] = []
    for p in pts:
        pid = p.get("id")
        if not pid:
            orphans.append(p)
            continue
        prev = best.get(str(pid))
        if prev is None or float(p.get("t") or 0) >= float(prev.get("t") or 0):
            best[str(pid)] = p
    out = list(best.values()) + orphans
    out.sort(key=lambda x: float(x.get("t") or 0), reverse=True)
    return out


def _build_own_tracks(pts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    Stitch dashed-map tracks from OUR samples only (history.db + live snapshot
    we stored, or sonde logs we parse). Never reads feeder globe_history / traces.
    """
    by_id: dict[str, list[dict[str, Any]]] = {}
    for p in pts:
        pid = p.get("id")
        if not pid:
            continue
        try:
            la = float(p["lat"])
            lo = float(p["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        if not math.isfinite(la) or not math.isfinite(lo):
            continue
        by_id.setdefault(str(pid), []).append(p)

    tracks: list[dict[str, Any]] = []
    for pid, group in by_id.items():
        if len(tracks) >= MAX_OWN_TRACKS:
            break
        group = sorted(group, key=lambda x: float(x.get("t") or 0))
        coords: list[list[float]] = []
        prev: tuple[float, float] | None = None
        for p in group:
            la = float(p["lat"])
            lo = float(p["lon"])
            t = float(p.get("t") or 0)
            if prev is not None and prev[0] == la and prev[1] == lo:
                continue
            coords.append([la, lo, t])
            prev = (la, lo)
        if len(coords) < 2:
            continue
        label = group[-1].get("label") or pid
        tracks.append(
            {
                "id": pid,
                "label": label,
                "coords": coords,
                # provenance: station map samples, not ultrafeeder/tar1090 traces
                "built_from": "station_map_samples",
            }
        )
    return tracks


def build_response(window_key: str, source_filter: set[str] | None) -> dict[str, Any]:
    if window_key not in WINDOWS:
        window_key = "24h"
    span = WINDOWS[window_key]
    t_max = _now()
    t_min = None if span is None else t_max - span

    wanted = [s for s in SOURCES if source_filter is None or s in source_filter]
    result_sources: dict[str, Any] = {}
    notes: list[str] = []

    # ── live collect + store ──────────────────────────────────────────────
    live_jobs: dict[str, Any] = {}
    need_adsc = "adsc" in wanted or any(s in wanted for s in PROTOCOL_SOURCES)
    adsc_buckets: dict[str, list[dict[str, Any]]] = {s: [] for s in PROTOCOL_SOURCES}
    adsc_buckets["_all"] = []
    adsc_meta: dict[str, Any] = {"ok": False}
    if need_adsc:
        adsc_buckets, adsc_meta = collect_adsc_split()

    if "adsb-1090" in wanted:
        live_jobs["adsb-1090"] = collect_aircraft_json(
            "http://127.0.0.1:8085/data/aircraft.json",
            id_prefix="h-",
            exclude_types=frozenset({"adsc"}),
        )
    if "adsc" in wanted:
        live_jobs["adsc"] = (adsc_buckets.get("_all") or [], adsc_meta)
    if "uat-978" in wanted:
        live_jobs["uat-978"] = collect_aircraft_json(
            "http://127.0.0.1:9780/skyaware978/data/aircraft.json", id_prefix="u-"
        )
    if "ais-162" in wanted:
        live_jobs["ais-162"] = collect_ais()
    if "adsb-mobile" in wanted:
        m1090, m1090_meta = collect_aircraft_json(MOBILE_ADSB_URL, id_prefix="m-")
        m978, m978_meta = collect_aircraft_json(MOBILE_UAT_URL, id_prefix="mu-")
        live_jobs["adsb-mobile"] = (m1090 + m978, {"adsb": m1090_meta, "uat": m978_meta, "ok": m1090_meta.get("ok") or m978_meta.get("ok")})

    # 1h: zero thinning on every mode (full samples). Longer windows keep caps / collapse.
    no_thin = window_key == "1h"

    for src, (pts, meta) in live_jobs.items():
        stored = _store_points(src, pts, dedupe_sec=0.0 if no_thin else None)
        meta["stored"] = stored
        hist = _load_history(src, t_min, t_max)
        dense = bool(SOURCES[src].get("dense"))
        if no_thin:
            merged = _concat_all_points(pts, hist)
            out_points = merged
            point_mode = "no_thin_1h"
        else:
            merge_cap = MAX_POINTS_DENSE if dense else MAX_POINTS_SPARSE
            merged = _merge_unique(pts, hist, merge_cap)
            latest = _latest_unique(pts) if pts else _latest_unique(merged)
            if dense:
                out_points = latest[:MAX_POINTS_DENSE]
                point_mode = "latest_per_aircraft"
            else:
                out_points = merged
                point_mode = "merged_capped"
        heat = _heat_bins(merged) if dense else []
        # Own tracks from our samples only (1h · ADS-B/UAT/mobile — never feeder traces)
        own_tracks: list[dict[str, Any]] = []
        if no_thin and src in TRACK_SOURCE_IDS:
            own_tracks = _build_own_tracks(merged)
        result_sources[src] = {
            "label": SOURCES[src]["label"],
            "color": SOURCES[src]["color"],
            "default_on": SOURCES[src]["default_on"],
            "icon": SOURCES[src].get("icon", "dot"),
            "mode": "heatmap" if dense else "points",
            "count": len(merged),
            "live_count": len(pts),
            "points": out_points,
            "tracks": own_tracks,
            "heat": heat,
            "meta": meta,
            "note": SOURCES[src].get("note"),
        }
        meta["point_mode"] = point_mode
        meta["history_samples"] = len(hist)
        meta["thin"] = {"enabled": not no_thin}
        meta["tracks"] = {
            "count": len(own_tracks),
            "built_from": "station_map_samples" if own_tracks else None,
        }
        if not meta.get("ok"):
            notes.append(f"{src}: collect failed ({meta.get('error', 'unknown')})")

    # ── history stores ────────────────────────────────────────────────────
    for src in wanted:
        if SOURCES[src]["kind"] not in ("history", "ham"):
            continue
        if src == "radiosonde-400":
            # 1h: every telemetry fix; 8h+ : time/distance thin so parachutes stay readable
            pts, meta = collect_sondes(t_min, t_max, thin=not no_thin)
        elif SOURCES[src].get("kind") == "ham":
            pts, meta = collect_ham(src, t_min, t_max, thin=not no_thin)
        else:
            pts, meta = collect_acars(src, t_min, t_max, thin=not no_thin)
            extra = list(adsc_buckets.get(src) or [])
            if src == "acars-iridium":
                side, side_meta = collect_irdm_sidecar(t_min, t_max)
                extra.extend(side)
                meta["sidecar"] = side_meta
            if extra:
                if src in PROTOCOL_SOURCES:
                    _store_points(src, extra, dedupe_sec=0.0 if no_thin else None)
                pts = extra + pts
                meta["live_adsc"] = len(adsc_buckets.get(src) or [])
            if no_thin:
                pass
            else:
                pts = _downsample(pts, MAX_POINTS_SPARSE)
        live_n = len(adsc_buckets.get(src) or [])
        own_tracks = (
            _build_own_tracks(pts)
            if (no_thin and src in TRACK_SOURCE_IDS)
            else []
        )
        result_sources[src] = {
            "label": SOURCES[src]["label"],
            "color": SOURCES[src]["color"],
            "default_on": SOURCES[src]["default_on"],
            "icon": SOURCES[src].get("icon", "dot"),
            "mode": "points",
            "count": len(pts),
            "live_count": live_n,
            "points": pts,
            "tracks": own_tracks,
            "heat": [],
            "meta": meta,
            "note": SOURCES[src].get("note"),
        }
        if own_tracks:
            meta["tracks"] = {
                "count": len(own_tracks),
                "built_from": "station_map_samples",
            }
        if not meta.get("ok"):
            notes.append(f"{src}: {meta.get('error', 'failed')}")

    # Include catalog entries that were filtered out so UI still knows defaults
    catalog = {
        sid: {
            "id": sid,
            "label": cfg["label"],
            "color": cfg["color"],
            "default_on": cfg["default_on"],
            "icon": cfg.get("icon", "dot"),
            "note": cfg.get("note"),
        }
        for sid, cfg in SOURCES.items()
    }

    total_pts = sum(s.get("count", 0) for s in result_sources.values())
    return {
        "station": {
            "name": STATION_NAME,
            "lat": STATION_LAT,
            "lon": STATION_LON,
        },
        "window": window_key,
        "window_seconds": span,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "t_min": t_min,
        "t_max": t_max,
        "sources": result_sources,
        "catalog": catalog,
        "total_points": total_pts,
        "notes": notes,
        "range_rings_nm": [50, 100, 150, 200, 250],
        "no_thin": no_thin,
        "hint": (
            "Live sources (ADS-B/UAT/AIS/mobile) only accumulate while this map is loaded "
            "and refreshed. ACARS positions and radiosonde tracks use existing station history. "
            "The 1h window applies no thinning on any mode."
            if no_thin
            else (
                "Live sources (ADS-B/UAT/AIS/mobile) only accumulate while this map is loaded "
                "and refreshed. ACARS positions and radiosonde tracks use existing station history."
            )
        ),
    }


def meta_response() -> dict[str, Any]:
    return {
        "station": {
            "name": STATION_NAME,
            "lat": STATION_LAT,
            "lon": STATION_LON,
        },
        "windows": list(WINDOWS.keys()),
        "default_window": "24h",
        "sources": [
            {
                "id": sid,
                "label": cfg["label"],
                "color": cfg["color"],
                "default_on": cfg["default_on"],
                "kind": cfg["kind"],
                "icon": cfg.get("icon", "dot"),
                "note": cfg.get("note"),
            }
            for sid, cfg in SOURCES.items()
        ],
        "mobile": {
            "host": MOBILE_HOST,
            "adsb_url": MOBILE_ADSB_URL,
            "uat_url": MOBILE_UAT_URL,
        },
    }


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"map-api {self.address_string()} {fmt % args}", flush=True)

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def _json(self, code: int, payload: Any) -> None:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        self.send_response(code)
        self._cors()
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path in ("/health", "/api/map/health"):
            self._json(200, {"ok": True, "service": "map-api"})
            return

        if path in ("/api/map/meta", "/api/map"):
            self._json(200, meta_response())
            return

        if path == "/api/map/points":
            window = (qs.get("window") or ["24h"])[0]
            src_raw = (qs.get("sources") or ["all"])[0]
            source_filter: set[str] | None
            if src_raw in ("", "all", "*"):
                source_filter = None
            else:
                source_filter = {s.strip() for s in src_raw.split(",") if s.strip()}
            try:
                payload = build_response(window, source_filter)
                self._json(200, payload)
            except Exception as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/map/ham":
            try:
                lim = int((qs.get("limit") or ["80"])[0])
            except ValueError:
                lim = 80
            self._json(200, ham_summary(max(1, min(lim, 500))))
            return

        self._json(
            404,
            {
                "error": "not found",
                "paths": ["/api/map/points", "/api/map/meta", "/api/map/ham", "/health"],
            },
        )


def main() -> None:
    # Ensure history path is writable
    try:
        HISTORY_DB.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"map-api warn: history dir {e}", flush=True)
    httpd = ThreadingHTTPServer(LISTEN, Handler)
    print(f"map-api listening on {LISTEN[0]}:{LISTEN[1]}", flush=True)
    print(f"  ACARS_DB={ACARS_DB} exists={ACARS_DB.exists()}", flush=True)
    print(f"  SONDE_LOG={SONDE_LOG_DIR} exists={SONDE_LOG_DIR.exists()}", flush=True)
    print(f"  HISTORY_DB={HISTORY_DB}", flush=True)
    print(f"  IRDM_POS={IRDM_POS} exists={IRDM_POS.exists()}", flush=True)
    print(f"  MOBILE={MOBILE_ADSB_URL}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
