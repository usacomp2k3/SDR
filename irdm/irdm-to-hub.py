#!/usr/bin/env python3
"""Pass through iridium-toolkit JSON to Airframes; send hub-shaped JSON to acars_router.

libacars mode uses acars.msg_text / reg / blk_id. ACARS Hub's IRDM formatter
only copies text / tail / block_id, so rows stopped landing. Flatten to the
fields the hub insert already understands (no new columns). Do not set
app.name=iridium-toolkit on the hub copy or formatIrdmMessage will strip
libacars / lat / lon.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import time
from datetime import datetime, timezone


def _parse_ts(val) -> float | None:
    if isinstance(val, (int, float)) and val > 1e8:
        return float(val)
    if isinstance(val, str) and val:
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def _walk_pos(obj):
    if isinstance(obj, dict):
        lat = obj.get("lat", obj.get("latitude"))
        lon = obj.get("lon", obj.get("longitude"))
        if lat is not None and lon is not None:
            try:
                lat_f, lon_f = float(lat), float(lon)
            except (TypeError, ValueError):
                lat_f = lon_f = None
            else:
                if abs(lat_f) <= 90 and abs(lon_f) <= 180 and not (lat_f == 0 and lon_f == 0):
                    alt = obj.get("alt", obj.get("altitude"))
                    return lat_f, lon_f, alt
        for v in obj.values():
            found = _walk_pos(v)
            if found:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = _walk_pos(v)
            if found:
                return found
    return None


def normalize(j: dict) -> dict:
    """Old iridium-toolkit ACARS JSON — what acars_router counts as IRDM."""
    ac_in = j.get("acars") if isinstance(j.get("acars"), dict) else {}
    src_in = j.get("source") if isinstance(j.get("source"), dict) else {}
    text = ac_in.get("text") or ac_in.get("msg_text") or ""
    tail = str(ac_in.get("tail") or ac_in.get("reg") or "").lstrip(".")
    ts = ac_in.get("timestamp") or j.get("timestamp")
    if isinstance(ts, (int, float)):
        ts = datetime.fromtimestamp(float(ts), tz=timezone.utc).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    elif not isinstance(ts, str) or not ts:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    err = ac_in.get("errors")
    if err is None:
        err = 1 if ac_in.get("err") else 0
    if "more" in ac_in:
        block_end = not bool(ac_in["more"])
    else:
        block_end = bool(ac_in.get("block_end", True))

    ac = {
        "timestamp": ts,
        "errors": int(err) if err is not None else 0,
        "link_direction": j.get("link_direction") or ac_in.get("link_direction") or "downlink",
        "block_end": block_end,
        "mode": ac_in.get("mode") or "2",
        "tail": tail,
        "label": str(ac_in.get("label") or "").replace("\x7f", "d"),
        "block_id": ac_in.get("block_id") or ac_in.get("blk_id") or "",
        "ack": ac_in.get("ack") or "",
        "text": text,
    }
    if ac_in.get("flight"):
        ac["flight"] = ac_in["flight"]
    if ac_in.get("sublabel"):
        ac["sublabel"] = ac_in["sublabel"]

    out: dict = {
        "app": {"name": "iridium-toolkit", "version": "0.0.1"},
        "source": {
            "transport": "iridium",
            "protocol": "acars",
            "station_id": src_in.get("station_id")
            or os.environ.get("STATION_ID")
            or "WP-KMCO-IRDM",
        },
        "acars": ac,
        # acars_vdlm2_parser::IrdmMessage requires header (not Option).
        # Without it the router silently drops the datagram.
        "header": j["header"] if isinstance(j.get("header"), str) else "",
    }
    if isinstance(j.get("freq"), (int, float)):
        out["freq"] = float(j["freq"])
    if isinstance(j.get("level"), (int, float)):
        out["level"] = float(j["level"])
    return out


def _sidecar_path() -> str:
    return os.environ.get("IRDM_POS_LOG", "/opt/logs/irdm-pos.jsonl")


def _write_pos(msg: dict, hub: dict) -> None:
    """Router IRDM schema has no lat/lon. Keep a sidecar for the portal map."""
    found = _walk_pos(msg)
    if not found:
        return
    lat, lon, alt = found
    ac = hub.get("acars") if isinstance(hub.get("acars"), dict) else {}
    ts = _parse_ts(ac.get("timestamp") or msg.get("timestamp")) or time.time()
    rec = {
        "t": ts,
        "lat": lat,
        "lon": lon,
        "id": (ac.get("tail") or "").strip() or None,
        "label": (ac.get("flight") or ac.get("tail") or "").strip() or None,
        "label_acars": ac.get("label") or "",
    }
    if alt is not None:
        rec["alt"] = alt
    path = _sidecar_path()
    try:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, separators=(",", ":")) + "\n")
    except OSError:
        return
    try:
        if os.path.getsize(path) > 2_000_000:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()[-1500:]
            with open(path, "w", encoding="utf-8") as fh:
                fh.writelines(lines)
    except OSError:
        pass


def main() -> None:
    dest = os.environ.get("IRDM_HUB", "acars_router:5558")
    host, port_s = dest.rsplit(":", 1)
    port = int(port_s)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 0
    for line in sys.stdin:
        sys.stdout.write(line)
        sys.stdout.flush()
        raw = line.strip()
        if not raw.startswith("{"):
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(msg, dict):
            continue
        try:
            hub = normalize(msg)
        except Exception:
            continue
        _write_pos(msg, hub)
        try:
            sock.sendto(
                (json.dumps(hub, separators=(",", ":")) + "\n").encode("utf-8"),
                (host, port),
            )
            sent += 1
        except OSError:
            pass
    _ = sent


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
