"""Maidenhead <-> lat/lon. 4-char ~100 km, 6-char ~5 km."""

from __future__ import annotations


def to_maiden(lat: float, lon: float) -> str:
    lon = lon + 180.0
    lat = lat + 90.0
    a = ord("A")
    c0 = chr(a + int(lon // 20))
    c1 = chr(a + int(lat // 10))
    lon %= 20
    lat %= 10
    c2 = str(int(lon // 2))
    c3 = str(int(lat // 1))
    lon %= 2
    lat %= 1
    c4 = chr(a + int(lon * 12))
    c5 = chr(a + int(lat * 24))
    return f"{c0}{c1}{c2}{c3}{c4}{c5}"


def from_maiden(grid: str) -> tuple[float, float] | None:
    g = (grid or "").strip().upper()
    if len(g) < 4:
        return None
    if not (g[0].isalpha() and g[1].isalpha() and g[2].isdigit() and g[3].isdigit()):
        return None
    if g in {"RR73", "RRR", "73"}:
        return None
    lon = (ord(g[0]) - ord("A")) * 20 - 180
    lat = (ord(g[1]) - ord("A")) * 10 - 90
    lon += int(g[2]) * 2
    lat += int(g[3]) * 1
    if len(g) >= 6 and g[4].isalpha() and g[5].isalpha():
        lon += (ord(g[4]) - ord("A") + 0.5) / 12
        lat += (ord(g[5]) - ord("A") + 0.5) / 24
    else:
        lon += 1.0
        lat += 0.5
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        return None
    return lat, lon
