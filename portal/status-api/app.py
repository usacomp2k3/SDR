#!/usr/bin/env python3
"""
WP-KMCO aggregator / feeder status API.
Listens on :8881  →  GET /api/status  GET /health

Reports every well-known public aggregator for each mode, including those
not currently configured (status=off). Active feeds are probed via Docker
+ local HTTP / logs.

Also serves GET /api/sdr — USB / radio inventory from Equipmentlist.md.
"""
from __future__ import annotations

import json
import re
import socket
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from sdr import collect_sdr

SOCK = "/var/run/docker.sock"
LISTEN = ("0.0.0.0", 8881)
TIMEOUT = 2.5

# ── Catalog: public aggregators (even if not configured here) ───────────────
# kind: airframes_mode | ultrafeeder_host | commercial | ais | sonde | other
# match: substring(s) in ULTRAFEEDER_CONFIG or container/env indicators

CATALOG: list[dict[str, Any]] = [
    # Aviation datalink — Airframes (public)
    {
        "id": "airframes-acars",
        "mode": "ACARS",
        "name": "Airframes ACARS",
        "destination": "feed.airframes.io:5550 (UDP)",
        "link": "https://app.airframes.io/",
        "kind": "airframes",
        "station": "WP-KMCO-ACARS",
        "containers": ["acarsdec", "acars_router"],
        "rate_key": "acarshub_rrd_acars_messages_per_minute",
    },
    {
        "id": "airframes-vdl2",
        "mode": "VDL2",
        "name": "Airframes VDL2",
        "destination": "feed.airframes.io:5553 (TCP)",
        "link": "https://app.airframes.io/",
        "kind": "airframes",
        "station": "WP-KMCO-VDL2",
        "containers": ["dumpvdl2", "acars_router"],
        "rate_key": "acarshub_rrd_vdlm_messages_per_minute",
    },
    {
        "id": "airframes-hfdl",
        "mode": "HFDL",
        "name": "Airframes HFDL",
        "destination": "feed.airframes.io:5556 (TCP)",
        "link": "https://app.airframes.io/",
        "kind": "airframes",
        "station": "WP-KMCO-HFDL",
        "containers": ["dumphfdl", "acars_router"],
        "rate_key": "acarshub_rrd_hfdl_messages_per_minute",
    },
    {
        "id": "airframes-irdm",
        "mode": "Iridium",
        "name": "Airframes Iridium ACARS",
        "destination": "feed.airframes.io:5590 (TCP)",
        "link": "https://app.airframes.io/",
        "kind": "airframes",
        "station": "WP-KMCO-IRDM",
        "containers": ["irdm", "acars_router"],
        "rate_key": "acarshub_rrd_irdm_messages_per_minute",
    },
    {
        "id": "psk-reporter",
        "mode": "HF ham",
        "name": "PSK Reporter (FT8/FT4/WSPR)",
        "destination": "report.pskreporter.info (TCP)",
        "link": "https://pskreporter.info/pskmap.html",
        "kind": "ham_psk",
        "station": "KQ4ORY",
    },
    {
        "id": "wsprnet",
        "mode": "HF ham",
        "name": "WSPRnet",
        "destination": "wsprnet.org/post",
        "link": "https://wsprnet.org/",
        "kind": "ham_psk",
        "station": "KQ4ORY",
        "note": "ALL_WSPR.TXT POST as KQ4ORY / EL98ho",
    },
    # Other ACARS-family public endpoints sometimes used
    {
        "id": "acarsdrama",
        "mode": "ACARS",
        "name": "ACARS Drama",
        "destination": "feedthe.acarsdrama.com:5550/5555 (UDP ACARS+VDL2)",
        "link": "https://acarsdrama.com/",
        "kind": "acars_router",
        "hosts": ["acarsdrama.com", "feedthe.acarsdrama.com"],
        "container": "acars_router",
        "note": "JSON UDP; email feeders@acarsdrama.com to confirm station",
    },
    {
        "id": "adsblol-acars",
        "mode": "ACARS",
        "name": "adsb.lol ACARS",
        "destination": "feed-acars.adsb.lol:5550/5551/5552 (TCP ACARS/HFDL/VDL2)",
        "link": "https://adsb.lol/",
        "kind": "acars_router",
        "hosts": ["feed-acars.adsb.lol", "adsb.lol"],
        "container": "acars_router",
        "note": "Datalink path (separate from ADS-B ultrafeeder feed)",
    },
    {
        "id": "avdelphi-acars",
        "mode": "ACARS",
        "name": "AVDelphi ACARS",
        "destination": "data.avdelphi.com:5556/5600 (UDP ACARS+VDL2)",
        "link": "https://www.avdelphi.com/myfeed_acars.html",
        "kind": "acars_router",
        "hosts": ["data.avdelphi.com", "avdelphi.com"],
        "container": "acars_router",
        "note": "Claim feed on AVDelphi after data is flowing",
    },
    {
        "id": "flightdeck-acars",
        "mode": "ACARS",
        "name": "FlightDeck ACARS",
        "destination": "acars.tryflightdeck.com:5550/5555 (UDP ACARS+VDL2)",
        "link": "https://www.tryflightdeck.com/coverage",
        "kind": "acars_router",
        "hosts": ["acars.tryflightdeck.com"],
        "container": "acars_router",
        "note": "Official FlightDeck ingest; ADS-B feed is separate (ultrafeeder)",
    },
    {
        "id": "adsbitalia-acars",
        "mode": "ACARS",
        "name": "ADSBItalia ACARS",
        "destination": "feed.adsbitalia.it:31109 (TCP)",
        "link": "https://www.adsbitalia.it/",
        "kind": "acars_router",
        "hosts": ["feed.adsbitalia.it", "adsbitalia.it"],
        "container": "acars_router",
        "note": "VHF ACARS JSON; ADS-B/MLAT is separate (ultrafeeder :31108/:41113)",
    },
    # AIS — public / common
    {
        "id": "ais-airframes",
        "mode": "AIS",
        "name": "Airframes AIS",
        "destination": "Airframes AIS ingest",
        "link": "https://app.airframes.io/",
        "kind": "ais_env",
        "env_any": ["AIRFRAMES_STATION_ID"],
        "container": "shipfeeder",
    },
    {
        "id": "ais-aiscatcher",
        "mode": "AIS",
        "name": "aiscatcher.org",
        "destination": "aiscatcher.org community",
        "link": "https://aiscatcher.org/",
        "kind": "ais_env",
        "env_true": ["AISCATCHER_SHAREDATA"],
        "container": "shipfeeder",
    },
    {
        "id": "ais-boatbeacon",
        "mode": "AIS",
        "name": "BoatBeacon",
        "destination": "BoatBeacon share",
        "kind": "ais_env",
        "env_true": ["BOATBEACON_SHAREDATA"],
        "container": "shipfeeder",
    },
    {
        "id": "ais-shipfinder",
        "mode": "AIS",
        "name": "ShipFinder",
        "destination": "ShipFinder share",
        "kind": "ais_env",
        "env_true": ["SHIPFINDER_SHAREDATA"],
        "container": "shipfeeder",
    },
    {
        "id": "ais-marinetraffic",
        "mode": "AIS",
        "name": "MarineTraffic",
        "destination": "MarineTraffic (keyed UDP)",
        "link": "https://www.marinetraffic.com/",
        "kind": "ais_env",
        "env_any": ["MARINETRAFFIC_UDP_PORT", "AIS_MARINETRAFFIC_UDP_PORT"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-vesselfinder",
        "mode": "AIS",
        "name": "VesselFinder",
        "destination": "VesselFinder (keyed UDP)",
        "link": "https://www.vesselfinder.com/",
        "kind": "ais_env",
        "env_any": ["VESSELFINDER_UDP_PORT", "AIS_VESSELFINDER_UDP_PORT"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-aishub",
        "mode": "AIS",
        "name": "AISHub",
        "destination": "AISHub (keyed UDP)",
        "link": "https://www.aishub.net/",
        "kind": "ais_env",
        "env_any": ["AISHUB_UDP_PORT"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-shipxplorer",
        "mode": "AIS",
        "name": "ShipXplorer",
        "destination": "ShipXplorer (keyed)",
        "link": "https://www.shipxplorer.com/",
        "kind": "ais_env",
        "env_any": ["SHIPXPLORER_UDP_PORT", "AIS_SHIPXPLORER_UDP_PORT"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-myshiptracking",
        "mode": "AIS",
        "name": "MyShipTracking",
        "destination": "MyShipTracking (keyed UDP)",
        "link": "https://www.myshiptracking.com/",
        "kind": "ais_env",
        "env_any": ["MYSHIPTRACKING_UDP_PORT", "AIS_MYSHIPTRACKING_UDP_PORT"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-vesseltracker",
        "mode": "AIS",
        "name": "VesselTracker",
        "destination": "VesselTracker (keyed UDP)",
        "link": "https://www.vesseltracker.com/",
        "kind": "ais_env",
        "env_any": ["VESSELTRACKER_UDP_PORT", "AIS_VESSELTRACKER_UDP_PORT"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-hpradar",
        "mode": "AIS",
        "name": "HPRadar AIS",
        "destination": "HPRadar AIS UDP",
        "kind": "ais_env",
        "env_any": ["AIS_HPRADAR_UDP_PORT", "HPRADAR_UDP_PORT"],
        "container": "shipfeeder",
    },
    {
        "id": "ais-aprsfi",
        "mode": "AIS",
        "name": "aprs.fi (AIS)",
        "destination": "aprs.fi feeder key",
        "link": "https://aprs.fi/",
        "kind": "ais_env",
        "env_any": ["APRSFI_FEEDER_KEY", "AIS_APRSFI_FEEDER_KEY"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    {
        "id": "ais-sdrmap",
        "mode": "AIS",
        "name": "sdrmap (AIS)",
        "destination": "ais.feed.sdrmap.org",
        "link": "https://sdrmap.org/",
        "kind": "ais_env",
        "env_any": ["SDRMAP_STATION_ID", "FEEDER_SDRMAP_USERNAME"],
        "container": "shipfeeder",
        "needs_key": True,
    },
    # Radiosonde
    {
        "id": "sondehub",
        "mode": "Radiosonde",
        "name": "SondeHub",
        "destination": "api.v2.sondehub.org",
        "link": "https://tracker.sondehub.org/",
        "kind": "sonde",
        "container": "radiosonde_auto_rx",
    },
    {
        "id": "sondehub-amateur",
        "mode": "Radiosonde",
        "name": "SondeHub Amateur",
        "destination": "amateur.sondehub.org",
        "link": "https://amateur.sondehub.org/",
        "kind": "off_default",
        "note": "Not configured (habhub amateur path)",
    },
    {
        "id": "aprs-sonde",
        "mode": "Radiosonde",
        "name": "APRS-IS (sonde)",
        "destination": "APRS-IS sonde beacons",
        "kind": "off_default",
        "note": "APRS upload disabled in station.cfg",
    },
    # ADS-B commercial (public networks)
    {
        "id": "adsb-flightaware",
        "mode": "ADS-B",
        "name": "FlightAware",
        "destination": "piaware → FlightAware (+ UAT)",
        "link": "https://flightaware.com/adsb/stats/",
        "kind": "commercial",
        "container": "piaware",
        "probe": "piaware",
    },
    {
        "id": "adsb-fr24",
        "mode": "ADS-B",
        "name": "Flightradar24",
        "destination": "fr24feed (+ UAT key)",
        "link": "https://www.flightradar24.com/account/data-sharing",
        "kind": "commercial",
        "container": "fr24feed",
        "probe": "fr24",
    },
    {
        "id": "adsb-radarbox",
        "mode": "ADS-B",
        "name": "AirNav Radar",
        "destination": "rbfeeder / RadarBox",
        "link": "https://www.airnavradar.com/",
        "kind": "commercial",
        "container": "rbfeeder",
        "probe": "rbfeeder",
    },
    {
        "id": "adsb-opensky",
        "mode": "ADS-B",
        "name": "OpenSky Network",
        "destination": "opensky-network.org",
        "link": "https://opensky-network.org/",
        "kind": "commercial",
        "container": "opensky",
        "probe": "opensky",
    },
    {
        "id": "adsb-planefinder",
        "mode": "ADS-B",
        "name": "PlaneFinder",
        "destination": "planefinder client",
        "link": "https://planefinder.net/",
        "kind": "commercial",
        "container": "planefinder",
        "probe": "planefinder",
    },
    {
        "id": "adsb-planewatch",
        "mode": "ADS-B",
        "name": "plane.watch",
        "destination": "feed.push.plane.watch",
        "link": "https://plane.watch/",
        "kind": "commercial",
        "container": "planewatch",
        "probe": "planewatch",
    },
    {
        "id": "adsb-adsbhub",
        "mode": "ADS-B",
        "name": "ADSBHub",
        "destination": "www.adsbhub.org",
        "link": "https://www.adsbhub.org/",
        "kind": "commercial",
        "container": "adsbhub",
        "probe": "adsbhub",
    },
    {
        "id": "adsb-sdrmap",
        "mode": "ADS-B",
        "name": "sdrmap",
        "destination": "sdrmap.org (1090 + MLAT; sonde logs via same container)",
        "link": "https://sdrmap.org/",
        "kind": "commercial",
        "container": "sdrmap",
        "probe": "sdrmap",
    },
    {
        "id": "adsb-radarvirtuel",
        "mode": "ADS-B",
        "name": "RadarVirtuel",
        "destination": "radarvirtuel.com",
        "link": "https://www.radarvirtuel.com/",
        "kind": "off_default",
        "note": "Not configured (no feeder key)",
        "container": "radarvirtuel",
    },
    {
        "id": "adsb-1090uk",
        "mode": "ADS-B",
        "name": "1090MHz UK",
        "destination": "1090mhz.uk",
        "link": "https://1090mhz.uk/",
        "kind": "off_default",
        "note": "Not configured",
    },
    {
        "id": "adsb-sdrmap",
        "mode": "ADS-B",
        "name": "SDRMap",
        "destination": "sdrmap.org",
        "link": "https://sdrmap.org/",
        "kind": "off_default",
        "note": "Not configured",
    },
    # ADS-B community (ultrafeeder hosts) — active if hostname in ULTRAFEEDER_CONFIG
    {
        "id": "uf-adsbfi",
        "mode": "ADS-B",
        "name": "adsb.fi",
        "destination": "feed.adsb.fi:30004 + mlat",
        "link": "https://adsb.fi/",
        "kind": "ultrafeeder",
        "hosts": ["feed.adsb.fi", "adsb.fi"],
    },
    {
        "id": "uf-adsblol",
        "mode": "ADS-B",
        "name": "adsb.lol",
        "destination": "feed.adsb.lol / in.adsb.lol + mlat",
        "link": "https://adsb.lol/",
        "kind": "ultrafeeder",
        "hosts": ["feed.adsb.lol", "in.adsb.lol", "adsb.lol"],
    },
    {
        "id": "uf-airplaneslive",
        "mode": "ADS-B",
        "name": "airplanes.live",
        "destination": "feed.airplanes.live:30004 + mlat",
        "link": "https://airplanes.live/",
        "kind": "ultrafeeder",
        "hosts": ["feed.airplanes.live", "airplanes.live"],
    },
    {
        "id": "uf-adsbx",
        "mode": "ADS-B",
        "name": "ADS-B Exchange",
        "destination": "feed1.adsbexchange.com + mlat",
        "link": "https://globe.adsbexchange.com/",
        "kind": "ultrafeeder",
        "hosts": ["adsbexchange.com", "feed1.adsbexchange.com"],
    },
    {
        "id": "uf-planespotters",
        "mode": "ADS-B",
        "name": "Planespotters",
        "destination": "feed.planespotters.net + mlat",
        "link": "https://www.planespotters.net/",
        "kind": "ultrafeeder",
        "hosts": ["planespotters.net"],
    },
    {
        "id": "uf-theairtraffic",
        "mode": "ADS-B",
        "name": "The Air Traffic",
        "destination": "feed.theairtraffic.com + mlat",
        "link": "https://theairtraffic.com/",
        "kind": "ultrafeeder",
        "hosts": ["theairtraffic.com"],
    },
    {
        "id": "uf-avdelphi",
        "mode": "ADS-B",
        "name": "AVDelphi",
        "destination": "data.avdelphi.com:24999",
        "link": "https://www.avdelphi.com/",
        "kind": "ultrafeeder",
        "hosts": ["avdelphi.com"],
    },
    {
        "id": "uf-dataero",
        "mode": "ADS-B",
        "name": "ADS-B Dataero",
        "destination": "adsb.dataero.eu + mlat",
        "link": "https://adsb.dataero.eu/",
        "kind": "ultrafeeder",
        "hosts": ["dataero.eu"],
    },
    {
        "id": "uf-adsbitalia",
        "mode": "ADS-B",
        "name": "ADSBItalia",
        "destination": "adsbitalia.it + mlat",
        "link": "https://www.adsbitalia.it/",
        "kind": "ultrafeeder",
        "hosts": ["adsbitalia.it"],
    },
    {
        "id": "uf-flyitaly",
        "mode": "ADS-B",
        "name": "Fly Italy ADSB",
        "destination": "dati.flyitalyadsb.com + mlat",
        "link": "https://flyitalyadsb.com/",
        "kind": "ultrafeeder",
        "hosts": ["flyitalyadsb.com"],
    },
    {
        "id": "uf-adsbiq",
        "mode": "ADS-B",
        "name": "ADSB IQ",
        "destination": "feed.adsbiq.com + mlat",
        "link": "https://adsbiq.com/",
        "kind": "ultrafeeder",
        "hosts": ["adsbiq.com"],
    },
    {
        "id": "uf-flyoverhead",
        "mode": "ADS-B",
        "name": "Fly Overhead",
        "destination": "feed.flyoverhead.com",
        "link": "https://flyoverhead.com/",
        "kind": "ultrafeeder",
        "hosts": ["flyoverhead.com"],
    },
    {
        "id": "uf-flyrealtraffic",
        "mode": "ADS-B",
        "name": "Fly Real Traffic",
        "destination": "feed.flyrealtraffic.com",
        "link": "https://flyrealtraffic.com/",
        "kind": "ultrafeeder",
        "hosts": ["flyrealtraffic.com"],
    },
    {
        "id": "uf-flightdeck",
        "mode": "ADS-B",
        "name": "FlightDeck",
        "destination": "feed.tryflightdeck.com",
        "link": "https://tryflightdeck.com/",
        "kind": "ultrafeeder",
        "hosts": ["tryflightdeck.com", "flightdeck"],
    },
    {
        "id": "uf-hpradar",
        "mode": "ADS-B",
        "name": "HPRadar",
        "destination": "skyfeed.hpradar.com + mlat",
        "link": "https://hpradar.com/",
        "kind": "ultrafeeder",
        "hosts": ["hpradar.com"],
    },
    {
        "id": "uf-alive",
        "mode": "ADS-B",
        "name": "airplanes.live (globe)",
        "destination": "Same as airplanes.live / alive map UUID",
        "link": "https://globe.airplanes.live/",
        "kind": "ultrafeeder",
        "hosts": ["airplanes.live"],
        "alias_of": "uf-airplaneslive",
    },
    {
        "id": "uf-radarplane",
        "mode": "ADS-B",
        "name": "RadarPlane",
        "destination": "feed.radarplane.com:30001 (historically)",
        "link": "https://radarplane.com/",
        "kind": "off_default",
        "note": "feed.radarplane.com is NXDOMAIN (service appears offline as of 2026-08)",
    },
    {
        "id": "uf-aussieadsb",
        "mode": "ADS-B",
        "name": "AussieADSB",
        "destination": "aussieadsb.com (Oceania)",
        "link": "https://aussieadsb.com/",
        "kind": "ultrafeeder",
        "hosts": ["aussieadsb.com"],
    },
    {
        "id": "uf-adsbone",
        "mode": "ADS-B",
        "name": "ADSB One",
        "destination": "feed.adsb.one:64004 + mlat :64006",
        "link": "https://adsb.one/",
        "kind": "ultrafeeder",
        "hosts": ["feed.adsb.one", "adsb.one"],
    },
    {
        "id": "uf-mapflights",
        "mode": "ADS-B",
        "name": "map.flights",
        "destination": "feed.map.flights:30004 (no MLAT)",
        "link": "https://map.flights/",
        "kind": "ultrafeeder",
        "hosts": ["feed.map.flights", "map.flights"],
        "note": "Claim UUID at map.flights/receivers; no MLAT server yet",
    },
]


def _http_get(url: str, timeout: float = TIMEOUT) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"User-Agent": "wp-kmco-status/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() if e.fp else b""
    except Exception:
        return 0, b""


def _docker_raw(path: str) -> bytes:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(TIMEOUT)
    s.connect(SOCK)
    s.sendall(f"GET {path} HTTP/1.0\r\nHost: localhost\r\n\r\n".encode())
    data = b""
    while True:
        chunk = s.recv(65536)
        if not chunk:
            break
        data += chunk
    s.close()
    if b"\r\n\r\n" not in data:
        return b""
    return data.split(b"\r\n\r\n", 1)[1]


def _docker(path: str) -> Any:
    try:
        body = _docker_raw(path)
        return json.loads(body.decode("utf-8", errors="replace")) if body else None
    except Exception:
        return None


def _docker_logs(name: str, tail: int = 80) -> str:
    path = f"/containers/{name}/logs?stdout=1&stderr=1&tail={tail}&timestamps=0"
    try:
        body = _docker_raw(path)
        out = []
        i = 0
        while i + 8 <= len(body):
            size = int.from_bytes(body[i + 4 : i + 8], "big")
            i += 8
            if size <= 0 or i + size > len(body):
                return body.decode("utf-8", errors="replace")
            out.append(body[i : i + size].decode("utf-8", errors="replace"))
            i += size
        return "".join(out) if out else body.decode("utf-8", errors="replace")
    except Exception:
        return ""


def _docker_env(name: str) -> dict[str, str]:
    try:
        c = _docker(f"/containers/{name}/json")
        if not c:
            return {}
        env: dict[str, str] = {}
        for e in (c.get("Config") or {}).get("Env") or []:
            if "=" in e:
                k, v = e.split("=", 1)
                env[k] = v
        return env
    except Exception:
        return {}


def _parse_prom(text: str) -> dict[str, float]:
    out: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([a-zA-Z0-9_:]+)(?:\{[^}]*\})?\s+([0-9.eE+-]+)\s*$", line)
        if m:
            try:
                out[m.group(1)] = float(m.group(2))
            except ValueError:
                pass
    return out


def _ctr_map() -> dict[str, dict]:
    raw = _docker("/containers/json?all=1")
    if not isinstance(raw, list):
        return {}
    m: dict[str, dict] = {}
    for c in raw:
        names = c.get("Names") or []
        name = (names[0] if names else "").lstrip("/")
        if not name:
            continue
        state = (c.get("State") or "").lower()
        status = c.get("Status") or state
        health = "none"
        sl = status.lower()
        if "(healthy)" in sl:
            health = "healthy"
        elif "(unhealthy)" in sl:
            health = "unhealthy"
        elif "health: starting" in sl:
            health = "starting"
        m[name] = {
            "running": state == "running",
            "health": health,
            "status": status,
            "state": state,
        }
    return m


def _item(
    *,
    id: str,
    mode: str,
    name: str,
    destination: str,
    status: str,
    detail: str,
    container: str | None = None,
    rate: str | None = None,
    link: str | None = None,
    configured: bool = True,
) -> dict:
    return {
        "id": id,
        "mode": mode,
        "name": name,
        "destination": destination,
        "status": status,  # ok | quiet | issue | down | unknown | off
        "detail": detail,
        "container": container,
        "rate": rate,
        "link": link,
        "configured": configured,
    }


def _env_truthy(env: dict[str, str], keys: list[str]) -> bool:
    for k in keys:
        v = (env.get(k) or "").strip().lower()
        if v in ("1", "true", "yes", "on", "enabled"):
            return True
    return False


def _env_set(env: dict[str, str], keys: list[str]) -> bool:
    for k in keys:
        v = (env.get(k) or "").strip()
        if v and v.lower() not in ("false", "0", "no", "off", "none", "null"):
            return True
    return False


def _uf_hosts_active(uf_config: str) -> set[str]:
    """Hostnames that appear as feed destinations in ULTRAFEEDER_CONFIG."""
    active: set[str] = set()
    if not uf_config:
        return active
    for part in uf_config.split(";"):
        part = part.strip()
        if not part:
            continue
        bits = part.split(",")
        if len(bits) < 2:
            continue
        kind = bits[0].strip().lower()
        host = bits[1].strip().lower()
        # skip local ingest sources
        if host in ("airspy_adsb", "dump978", "localhost", "127.0.0.1", "piaware", "rbfeeder", "planewatch", "sdrmap"):
            continue
        if kind in ("adsb", "mlat", "mlathub"):
            active.add(host)
            # also bare domain labels
            for label in host.split("."):
                if len(label) > 3:
                    active.add(label)
    return active


def _host_configured(hosts: list[str], active: set[str], uf_config: str) -> bool:
    cfg = (uf_config or "").lower()
    for h in hosts:
        hl = h.lower()
        if hl in cfg:
            return True
        if hl in active:
            return True
        for a in active:
            # require a real hostname-ish token; "adsb" matching aussieadsb.com is a false hit
            if len(a) < 8:
                continue
            if hl in a or a in hl:
                return True
    return False


def collect() -> dict:
    t0 = time.time()
    ctrs = _ctr_map()
    items: list[dict] = []

    def cinfo(name: str) -> dict:
        return ctrs.get(name) or {
            "running": False,
            "health": "none",
            "status": "missing",
            "state": "missing",
        }

    def running_ok(name: str) -> bool:
        return bool(cinfo(name).get("running"))

    # Hub metrics
    hub_rates: dict[str, float] = {}
    code, body = _http_get("http://127.0.0.1:8080/metrics")
    if code == 200:
        hub_rates = _parse_prom(body.decode("utf-8", errors="replace"))

    # Ultrafeeder config + aircraft
    uf_env = _docker_env("ultrafeeder")
    uf_config = uf_env.get("ULTRAFEEDER_CONFIG") or ""
    uf_active = _uf_hosts_active(uf_config)

    # acars_router outbound env (all AR_SEND_* destinations)
    ar_env = _docker_env("acars_router")
    ar_send_blob = " ".join(
        v for k, v in ar_env.items() if k.startswith("AR_SEND_") and v
    ).lower()
    ac_count = None
    code, body = _http_get("http://127.0.0.1:8085/data/aircraft.json")
    if code == 200:
        try:
            ac_count = len(json.loads(body).get("aircraft") or [])
        except Exception:
            pass
    stats_1m = None
    code, body = _http_get("http://127.0.0.1:8085/data/stats.json")
    if code == 200:
        try:
            stats_1m = json.loads(body).get("last1min", {}).get("messages")
        except Exception:
            pass

    # Shipfeeder
    sf_env = _docker_env("shipfeeder")
    ships_n = None
    code, body = _http_get("http://127.0.0.1:8090/ships.json")
    if code == 200:
        try:
            ships = json.loads(body)
            ships_n = ships.get("count") if isinstance(ships, dict) else None
        except Exception:
            pass

    # ADS-B RF path rows (infrastructure, not public aggs — keep concise)
    for name, dest, cid in [
        ("airspy_adsb (1090)", "Airspy Mini → ultrafeeder", "airspy_adsb"),
        ("dump978 (UAT)", "RTL 978 → ultrafeeder", "dump978"),
        ("ultrafeeder", "tar1090 · graphs · mlat-hub · multi-feed", "ultrafeeder"),
    ]:
        c = cinfo(cid)
        if not c["running"]:
            st, det = "down", "container not running"
        elif c["health"] == "unhealthy":
            st, det = "issue", c["status"]
        else:
            st = "ok"
            det = c["status"]
            if cid == "ultrafeeder" and ac_count is not None:
                det = f"{ac_count} aircraft"
                if stats_1m is not None:
                    det += f" · {stats_1m} msgs/min"
        items.append(
            _item(
                id=f"rf-{cid}",
                mode="ADS-B RF",
                name=name,
                destination=dest,
                status=st,
                detail=det,
                container=cid,
                rate=f"{ac_count} ac" if cid == "ultrafeeder" and ac_count is not None else None,
                link="http://127.0.0.1:8085/" if cid == "ultrafeeder" else None,
            )
        )

    # Commercial probes cache
    commercial_status: dict[str, tuple[str, str]] = {}

    # piaware
    logs = _docker_logs("piaware", 40)
    if not running_ok("piaware"):
        commercial_status["piaware"] = ("off", "Container not running")
    else:
        m = re.search(
            r"(\d+) msgs recv'd from dump1090.*?(\d+) msgs sent to FlightAware", logs
        )
        if m or "successfully sent" in logs or "msgs sent to FlightAware" in logs:
            det = f"{m.group(1)} recv · {m.group(2)} sent" if m else "sending to FlightAware"
            commercial_status["piaware"] = ("ok", det)
        else:
            commercial_status["piaware"] = ("issue", "running; no recent send line")

    # fr24
    code, body = _http_get("http://127.0.0.1:8754/monitor.json")
    if not running_ok("fr24feed"):
        commercial_status["fr24"] = ("off", "Container not running")
    elif code == 200:
        try:
            j = json.loads(body)
            fs = (j.get("feed_status") or "").lower()
            alias = j.get("feed_alias") or "?"
            n_ac = j.get("feed_num_ac_tracked") or "?"
            if fs == "connected":
                commercial_status["fr24"] = ("ok", f"{alias} connected · {n_ac} aircraft")
            else:
                commercial_status["fr24"] = ("issue", f"status={fs} · {alias}")
        except Exception as e:
            commercial_status["fr24"] = ("issue", str(e))
    else:
        commercial_status["fr24"] = ("issue", "monitor.json unavailable")

    # rbfeeder — current image logs "Packets sent" / "Server: ready", not "Key accepted"
    logs = _docker_logs("rbfeeder", 80)
    if not running_ok("rbfeeder"):
        commercial_status["rbfeeder"] = ("off", "Container not running")
    elif re.search(r"Invalid sharing-key", logs, re.I):
        commercial_status["rbfeeder"] = ("issue", "invalid sharing key")
    elif re.search(
        r"Packets sent|Key accepted|RadarBox24 server OK|Server:\s+ready", logs
    ):
        bits = []
        m_pkt = re.search(r"Packets sent in past 5 minutes:\s+(\d+)", logs)
        m_rx = re.search(r"([\d.]+)\s+msg/s received", logs)
        if m_pkt:
            bits.append(f"{int(m_pkt.group(1)):,} pkt/5min")
        if m_rx:
            bits.append(f"{m_rx.group(1)} msg/s rx")
        commercial_status["rbfeeder"] = (
            "ok",
            "feeding" + (f" · {' · '.join(bits)}" if bits else ""),
        )
    else:
        commercial_status["rbfeeder"] = ("issue", cinfo("rbfeeder")["status"])

    # opensky
    logs = _docker_logs("opensky", 30)
    if not running_ok("opensky"):
        commercial_status["opensky"] = ("off", "Container not running")
    elif "currently online" in logs:
        m = re.search(r"([\d]+) bytes sent", logs)
        commercial_status["opensky"] = (
            "ok",
            "online" + (f" · {m.group(1)} B sent" if m else ""),
        )
    else:
        commercial_status["opensky"] = ("issue", cinfo("opensky")["status"])

    # planefinder — pfclient only logs NTP after start; use the local stats API
    logs = _docker_logs("planefinder", 30)
    code_pf, body_pf = _http_get("http://127.0.0.1:30053/ajax/stats")
    if not running_ok("planefinder"):
        commercial_status["planefinder"] = ("off", "Container not running")
    elif code_pf == 200:
        try:
            jpf = json.loads(body_pf)
            pps = jpf.get("total_modes_packets_ps") or 0
            bout = jpf.get("master_server_bytes_out") or 0
            if bout or pps:
                commercial_status["planefinder"] = (
                    "ok",
                    f"connected · {pps} pkt/s to PF",
                )
            else:
                commercial_status["planefinder"] = (
                    "issue",
                    "client up; no uplink yet",
                )
        except Exception as e:
            commercial_status["planefinder"] = ("issue", str(e))
    elif "location has been verified" in logs or "TCP connection established" in logs:
        commercial_status["planefinder"] = ("ok", "connected · location verified")
    else:
        commercial_status["planefinder"] = ("issue", cinfo("planefinder")["status"])

    # planewatch — pw-feeder colorizes logs: ADSB=\x1b[0mhealthy
    logs = _docker_logs("planewatch", 80)
    logs_plain = re.sub(r"\x1b\[[0-9;]*m", "", logs)
    if not running_ok("planewatch"):
        commercial_status["planewatch"] = ("off", "Container not running")
    elif re.search(r"ADSB=healthy", logs_plain) or "proto=BEAST" in logs_plain:
        commercial_status["planewatch"] = (
            "ok",
            "ADSB healthy"
            + (" · MLAT healthy" if re.search(r"MLAT=healthy", logs_plain) else ""),
        )
    elif "retrying" in logs or "terminated" in logs or "broken pipe" in logs.lower():
        if "feeding BEAST" in logs or "ADSB=healthy" in logs:
            commercial_status["planewatch"] = (
                "issue",
                "feeding with reconnects (remote often flaky)",
            )
        else:
            commercial_status["planewatch"] = ("issue", "connect/retry loop")
    elif "feeding BEAST" in logs:
        commercial_status["planewatch"] = ("ok", "feeding BEAST")
    else:
        commercial_status["planewatch"] = ("unknown", cinfo("planewatch")["status"])

    # adsbhub — client only reprints "connected" on state change; use last line
    logs = _docker_logs("adsbhub", 80)
    if not running_ok("adsbhub"):
        commercial_status["adsbhub"] = ("off", "Container not running")
    else:
        last_up = None
        for line in logs.splitlines():
            s = line.strip().lower()
            if s == "connected":
                last_up = True
            elif s == "not connected":
                last_up = False
        if last_up is True:
            commercial_status["adsbhub"] = (
                "ok",
                "connected to data.adsbhub.org:5001",
            )
        elif last_up is False:
            commercial_status["adsbhub"] = (
                "down",
                "not connected (check client key / account / station IP)",
            )
        elif re.search(r"(?i)broken pipe|connection reset|connection refused", logs):
            commercial_status["adsbhub"] = ("issue", "connection flapping")
        else:
            commercial_status["adsbhub"] = ("issue", cinfo("adsbhub")["status"])

    logs = _docker_logs("sdrmap", 80)
    if not running_ok("sdrmap"):
        commercial_status["sdrmap"] = ("off", "Container not running")
    elif re.search(r"(?i)(connected|feeding|mlat|sent)", logs):
        commercial_status["sdrmap"] = ("ok", "feeder running")
    elif re.search(r"(?i)(auth|password|denied|refused|error)", logs):
        commercial_status["sdrmap"] = ("issue", "running; check username/password")
    else:
        commercial_status["sdrmap"] = ("issue", cinfo("sdrmap")["status"])

    # sonde
    rs = cinfo("radiosonde_auto_rx")
    code_s, _ = _http_get("http://127.0.0.1:5000/")
    if not rs["running"]:
        sonde_st, sonde_det = "off", "Container not running"
    elif code_s == 200:
        sonde_st, sonde_det = "ok", "UI up · SondeHub configured (WP-KMCO-SONDE)"
    else:
        sonde_st, sonde_det = "issue", f"container up; UI HTTP {code_s or 'fail'}"

    seen_aliases: set[str] = set()

    for cat in CATALOG:
        cid = cat["id"]
        if cat.get("alias_of") and cat["alias_of"] in seen_aliases:
            continue  # skip duplicate airplanes.live globe row if main already listed
        kind = cat["kind"]
        base = dict(
            id=cid,
            mode=cat["mode"],
            name=cat["name"],
            destination=cat["destination"],
            link=cat.get("link"),
            container=cat.get("container"),
        )

        if kind == "airframes":
            missing = [c for c in cat["containers"] if not running_ok(c)]
            rpm = hub_rates.get(cat["rate_key"]) if cat.get("rate_key") else None
            rate_s = f"{rpm:.0f}/min" if rpm is not None else None
            parts = [f"station {cat.get('station', '')}".strip()]
            if missing:
                # Host ka9q / RX888 replaced these docker decoders; rate still proves the feed.
                if cat["id"] == "airframes-hfdl" and missing == ["dumphfdl"]:
                    if rpm is not None and rpm > 0:
                        st = "ok"
                        parts.append("RX888 host decoder (docker HF+ dumphfdl parked)")
                    else:
                        st = "quiet"
                        parts.append("docker dumphfdl parked; host RX888 quiet (no recent HFDL)")
                elif cat["id"] == "airframes-acars" and missing == ["acarsdec"]:
                    if rpm is not None and rpm > 0:
                        st = "ok"
                        parts.append("host ka9q acarsdec (docker acarsdec parked)")
                    else:
                        st = "quiet"
                        parts.append("docker acarsdec parked; host ka9q quiet (no recent ACARS)")
                elif cat["id"] == "airframes-vdl2" and missing == ["dumpvdl2"]:
                    if rpm is not None and rpm > 0:
                        st = "ok"
                        parts.append("host ka9q dumpvdl2 (docker dumpvdl2 parked)")
                    else:
                        st = "quiet"
                        parts.append("docker dumpvdl2 parked; host ka9q quiet (no recent VDL2)")
                else:
                    st = "down"
                    parts.append("containers down: " + ", ".join(missing))
            elif rpm is not None and rpm > 0:
                st = "ok"
                parts.append("local decode active → router → Airframes")
            elif rpm is not None:
                st = "quiet"
                parts.append("0 msgs/min locally — feed path is up; band idle or just restarted")
            else:
                st = "ok" if not missing else "down"
                parts.append("containers up")
            items.append(
                _item(
                    **base,
                    status=st,
                    detail="; ".join(p for p in parts if p),
                    rate=rate_s,
                    configured=True,
                )
            )
            continue

        if kind == "ham_psk":
            n = 0
            code_h, body_h = _http_get("http://127.0.0.1:8882/api/map/ham")
            if code_h == 200:
                try:
                    hs = json.loads(body_h.decode("utf-8", errors="replace"))
                    for _m, mv in (hs.get("modes") or {}).items():
                        n += int(mv.get("last_hour") or 0)
                except Exception:
                    n = 0
            who = cat.get("station") or "KQ4ORY"
            dest = "WSPRnet" if cid == "wsprnet" else "PSK Reporter"
            if n > 0:
                st, det = "ok", f"{who} EL98ho · {n} spots/h local → {dest}"
            else:
                st, det = "quiet", f"skimmer configured as {who}; waiting for spots → {dest}"
            items.append(
                _item(
                    **base,
                    status=st,
                    detail=det,
                    rate=f"{n}/h" if n else None,
                    configured=True,
                )
            )
            continue

        if kind == "off_default":
            # Check if container exists for commercial-ish off items
            cname = cat.get("container")
            if cname and running_ok(cname):
                items.append(
                    _item(
                        **base,
                        status="unknown",
                        detail="Container present but not fully wired in status probes",
                        configured=True,
                    )
                )
            else:
                items.append(
                    _item(
                        **base,
                        status="off",
                        detail=cat.get("note") or "Not configured on this station",
                        configured=False,
                    )
                )
            continue

        if kind == "ais_env":
            configured = False
            if cat.get("env_true"):
                configured = _env_truthy(sf_env, cat["env_true"])
            if cat.get("env_any"):
                configured = configured or _env_set(sf_env, cat["env_any"])
            # Airframes AIS: station id alone is enough
            if cat["id"] == "ais-airframes" and sf_env.get("AIRFRAMES_STATION_ID"):
                configured = True
            if not configured:
                note = "Not configured"
                if cat.get("needs_key"):
                    note = "Not configured (needs signup / UDP key)"
                items.append(
                    _item(
                        **base,
                        status="off",
                        detail=note,
                        configured=False,
                    )
                )
                continue
            if not running_ok("shipfeeder"):
                items.append(
                    _item(
                        **base,
                        status="down",
                        detail="Configured but shipfeeder not running",
                        configured=True,
                    )
                )
                continue
            if ships_n is not None and ships_n > 0:
                items.append(
                    _item(
                        **base,
                        status="ok",
                        detail=f"shipfeeder up · {ships_n} vessels in view",
                        rate=f"{ships_n} ships",
                        configured=True,
                    )
                )
            elif ships_n == 0:
                items.append(
                    _item(
                        **base,
                        status="quiet",
                        detail="Configured · 0 vessels currently",
                        configured=True,
                    )
                )
            else:
                items.append(
                    _item(
                        **base,
                        status="ok",
                        detail="Configured on shipfeeder",
                        configured=True,
                    )
                )
            continue

        if kind == "sonde":
            items.append(
                _item(
                    **base,
                    status=sonde_st if sonde_st != "off" else "off",
                    detail=sonde_det,
                    configured=sonde_st != "off",
                )
            )
            continue

        if kind == "commercial":
            probe = cat.get("probe") or ""
            if probe in commercial_status:
                st, det = commercial_status[probe]
                # container missing entirely → off not down if never deployed intent was config
                if st == "off" and not running_ok(cat.get("container") or ""):
                    # still "off" if we expected it from compose but it's not up — use down if compose has it
                    # For catalog, if container name known and image was part of station, show down if listed in docker but exited
                    c = cinfo(cat["container"])
                    if c["state"] == "missing":
                        st, det = "off", "Not deployed"
                    elif not c["running"]:
                        st, det = "down", c["status"]
                items.append(
                    _item(
                        **base,
                        status=st,
                        detail=det,
                        configured=st != "off",
                    )
                )
            else:
                items.append(
                    _item(
                        **base,
                        status="off",
                        detail="Not configured",
                        configured=False,
                    )
                )
            continue

        if kind == "acars_router":
            hosts = cat.get("hosts") or []
            configured = any(h.lower() in ar_send_blob for h in hosts)
            if not configured:
                items.append(
                    _item(
                        **base,
                        status="off",
                        detail=cat.get("note") or "Not in acars_router AR_SEND_*",
                        configured=False,
                    )
                )
                continue
            if not running_ok("acars_router"):
                items.append(
                    _item(
                        **base,
                        status="down",
                        detail="Configured but acars_router not running",
                        configured=True,
                    )
                )
                continue
            # Use hub rates as proxy that messages are flowing
            rpm = (
                hub_rates.get("acarshub_rrd_total_messages_per_minute")
                or hub_rates.get("acarshub_rrd_acars_messages_per_minute")
            )
            if rpm is not None and rpm > 0:
                items.append(
                    _item(
                        **base,
                        status="ok",
                        detail=f"In AR_SEND_* · ~{rpm:.0f} hub msgs/min local"
                        + (f" · {cat.get('note')}" if cat.get("note") else ""),
                        rate=f"{rpm:.0f}/min hub",
                        configured=True,
                    )
                )
            else:
                items.append(
                    _item(
                        **base,
                        status="quiet",
                        detail="Configured · little/no local message rate right now",
                        configured=True,
                    )
                )
            continue

        if kind == "ultrafeeder":
            hosts = cat.get("hosts") or []
            configured = _host_configured(hosts, uf_active, uf_config)
            if not configured:
                items.append(
                    _item(
                        **base,
                        status="off",
                        detail="Not in ULTRAFEEDER_CONFIG",
                        configured=False,
                    )
                )
                continue
            seen_aliases.add(cid)
            if not running_ok("ultrafeeder"):
                items.append(
                    _item(
                        **base,
                        status="down",
                        detail="In config but ultrafeeder not running",
                        configured=True,
                    )
                )
                continue
            if ac_count is not None and ac_count > 0:
                items.append(
                    _item(
                        **base,
                        status="ok",
                        detail=f"In ULTRAFEEDER_CONFIG · {ac_count} aircraft available to feed",
                        rate=f"{ac_count} ac",
                        configured=True,
                    )
                )
            else:
                items.append(
                    _item(
                        **base,
                        status="quiet",
                        detail="In config · no aircraft currently",
                        configured=True,
                    )
                )
            continue

        items.append(
            _item(
                **base,
                status="unknown",
                detail="Unhandled kind",
                configured=False,
            )
        )

    counts = {"ok": 0, "quiet": 0, "issue": 0, "down": 0, "unknown": 0, "off": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station": "WP-KMCO / WinterPark",
        "elapsed_ms": int((time.time() - t0) * 1000),
        "summary": counts,
        "items": items,
    }


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args) -> None:
        pass

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = self.path.split("?", 1)[0]
        if path in ("/health", "/"):
            body = b"ok\n"
            self.send_response(200)
            self._cors()
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if path in ("/api/status", "/api/sdr"):
            try:
                payload = collect_sdr(_ctr_map()) if path == "/api/sdr" else collect()
                raw = json.dumps(payload, indent=2).encode()
                self.send_response(200)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            except Exception as e:
                raw = json.dumps({"error": str(e)}).encode()
                self.send_response(500)
                self._cors()
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)
            return
        self.send_response(404)
        self.end_headers()


def main() -> None:
    httpd = ThreadingHTTPServer(LISTEN, Handler)
    print(f"status-api listening on {LISTEN[0]}:{LISTEN[1]}", flush=True)
    httpd.serve_forever()


if __name__ == "__main__":
    main()
