#!/usr/bin/env python3
"""WP-KMCO SDR / USB inventory for GET /api/sdr.

Expected devices come from Equipmentlist.md (## USB devices table).
"""
from __future__ import annotations

import os
import re
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SYS_USB = Path("/sys/bus/usb/devices")
SYS_USBCORE = Path("/sys/module/usbcore/parameters")
FW_PATH = Path("/lib/firmware/renesas_usb_fw.mem")
EQUIPMENT = Path(os.environ.get("SDR_EQUIPMENT_LIST", "/data/Equipmentlist.md"))

RATE_BY_DECODER = {
    "irdm": "acarshub_rrd_irdm_messages_per_minute",
    "acarsdec": "acarshub_rrd_acars_messages_per_minute",
    "dumpvdl2": "acarshub_rrd_vdlm_messages_per_minute",
    "radiod": "acarshub_rrd_hfdl_messages_per_minute",
}

# Host processes, not Docker container names (Equipmentlist.md Decoder column).
HOST_DECODERS = frozenset({"radiod", "vhf-r2", "meshtastic", "gpsd"})
# Which radiod.conf substring means this radio's ka9q instance.
RADIOD_CONF = {
    "radiod": "radiod-hfdl.conf",
    "vhf-r2": "vhf-r2/radiod.conf",
}

# PCI → friendly name on this chassis.
# Card A = PEG x16 4-port. Card B = PCH 4-port.
# Port numbers follow the four Renesas chips (03→06 and 0a→0d).
# OptiPlex 5060 SFF firmware labels the rear I/O shield as panel=top.
CONTROLLERS: dict[str, dict[str, str]] = {
    "0000:00:14.0": {
        "name": "Onboard Intel",
        "kind": "intel",
        "note": "",
    },
    "0000:03:00.0": {"name": "StarTech USB Card A · Port 1", "kind": "renesas", "card": "A", "port": "1"},
    "0000:04:00.0": {"name": "StarTech USB Card A · Port 2", "kind": "renesas", "card": "A", "port": "2"},
    "0000:05:00.0": {"name": "StarTech USB Card A · Port 3", "kind": "renesas", "card": "A", "port": "3"},
    "0000:06:00.0": {"name": "StarTech USB Card A · Port 4", "kind": "renesas", "card": "A", "port": "4"},
    "0000:0a:00.0": {"name": "StarTech USB Card B · Port 1", "kind": "renesas", "card": "B", "port": "1"},
    "0000:0b:00.0": {"name": "StarTech USB Card B · Port 2", "kind": "renesas", "card": "B", "port": "2"},
    "0000:0c:00.0": {"name": "StarTech USB Card B · Port 3", "kind": "renesas", "card": "B", "port": "3"},
    "0000:0d:00.0": {"name": "StarTech USB Card B · Port 4", "kind": "renesas", "card": "B", "port": "4"},
}

HUB_NAMES = {
    "1a40:0101": "Terminus USB 2 hub",
    "05e3:0610": "Genesys USB 2.1 hub",
    "05e3:0626": "Genesys USB 3.1 hub",
    "1d6b:0002": "USB 2.0 root hub",
    "1d6b:0003": "USB 3.x root hub",
}

KIND_ORDER = ["radio", "mesh", "gps", "hub", "amp", "unused"]
KIND_TITLE = {
    "radio": "Radios",
    "mesh": "Mesh",
    "gps": "GPS",
    "hub": "USB hubs",
    "amp": "USB-powered RF (no data)",
    "unused": "Unused / parked",
}


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def _norm_serial(raw: str) -> str:
    s = (raw or "").strip()
    if s.upper().startswith("AIRSPY SN:"):
        s = s.split(":", 1)[1]
    return s.strip()


def _blank(s: str | None) -> bool:
    v = (s or "").strip()
    return v == "" or v.upper() in ("TBD", "?", "NONE", "—", "-")


def _speed_label(speed: str) -> str:
    return {
        "1.5": "1.5M LS",
        "12": "12M FS",
        "480": "480M HS",
        "5000": "5G SS",
        "10000": "10G SS+",
    }.get(speed, f"{speed}M" if speed else "—")


def _parent_of(name: str) -> str | None:
    if name.startswith("usb"):
        return None
    if "." in name:
        return name.rsplit(".", 1)[0]
    return f"usb{name.split('-', 1)[0]}"


def _iface_drivers(dev: Path) -> list[str]:
    out: list[str] = []
    try:
        for iface in dev.iterdir():
            if ":" not in iface.name:
                continue
            try:
                out.append(os.path.basename(os.readlink(iface / "driver")))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _pci_of_usb_bus(bus_dir: Path) -> str:
    try:
        p = bus_dir.resolve()
        for _ in range(16):
            if (p / "vendor").exists() and (p / "device").exists() and ":" in p.name:
                return p.name
            if p.parent == p:
                break
            p = p.parent
    except Exception:
        pass
    return ""


def _pci_driver(pci: str) -> str:
    if not pci:
        return ""
    try:
        return os.path.basename(os.readlink(Path(f"/sys/bus/pci/devices/{pci}/driver")))
    except Exception:
        return ""


def _slug(role: str, hardware: str, serial: str, path: str) -> str:
    raw = serial or path or f"{role}-{hardware}"
    s = re.sub(r"[^a-zA-Z0-9]+", "-", raw).strip("-").lower()
    return s or "device"


def parse_equipment_usb(text: str) -> list[dict[str, Any]]:
    """Parse the markdown table under '## USB devices'."""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s+USB devices\b", line, re.I):
            start = i + 1
            break
    if start is None:
        return []

    header: list[str] | None = None
    rows: list[dict[str, Any]] = []
    for line in lines[start:]:
        if line.startswith("## "):
            break
        if not line.strip().startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if header is None:
            header = [c.lower() for c in cells]
            continue
        if all(re.fullmatch(r":?-{3,}:?", c or "") for c in cells):
            continue
        rec = {header[i]: (cells[i] if i < len(cells) else "") for i in range(len(header))}
        serial = rec.get("serial") or ""
        vid_pid = (rec.get("vid:pid") or rec.get("vid_pid") or "").lower()
        alt = []
        notes = rec.get("notes") or ""
        for m in re.finditer(r"\b([0-9a-f]{4}:[0-9a-f]{4})\b", notes, re.I):
            alt.append(m.group(1).lower())
        kind = (rec.get("kind") or "radio").strip().lower()
        decoder = (rec.get("decoder") or "").strip()
        path = (rec.get("path") or "").strip()
        role = rec.get("role") or ""
        hardware = rec.get("hardware") or ""
        rows.append(
            {
                "id": _slug(role, hardware, serial, path),
                "role": role,
                "hardware": hardware,
                "serial": "" if _blank(serial) else serial,
                "vid_pid": vid_pid,
                "alt_vid_pids": alt,
                "kind": kind,
                "decoder": decoder,
                "path": path,
                "notes": notes,
                "parked": kind == "unused",
                "no_data": kind == "amp" or "no data" in notes.lower(),
            }
        )
    return rows


def load_expected() -> tuple[list[dict[str, Any]], str]:
    if not EQUIPMENT.is_file():
        return [], f"Equipmentlist.md not mounted ({EQUIPMENT})"
    text = EQUIPMENT.read_text(encoding="utf-8", errors="replace")
    rows = parse_equipment_usb(text)
    if not rows:
        return [], "No ## USB devices table in Equipmentlist.md"
    return rows, str(EQUIPMENT)


def scan_usb() -> dict[str, dict[str, Any]]:
    devices: dict[str, dict[str, Any]] = {}
    if not SYS_USB.is_dir():
        return devices
    for d in SYS_USB.iterdir():
        name = d.name
        if ":" in name:
            continue
        vid = _read(d / "idVendor")
        pid = _read(d / "idProduct")
        if not vid or not pid:
            continue
        vid_pid = f"{vid}:{pid}"
        serial_raw = _read(d / "serial")
        drivers = _iface_drivers(d)
        is_root = name.startswith("usb")
        pci = _pci_of_usb_bus(d) if is_root else ""
        devices[name] = {
            "sys": name,
            "vid_pid": vid_pid,
            "bus": _read(d / "busnum"),
            "dev": _read(d / "devnum"),
            "speed": _read(d / "speed"),
            "manufacturer": _read(d / "manufacturer"),
            "product": _read(d / "product"),
            "serial": _norm_serial(serial_raw),
            "serial_raw": serial_raw,
            "drivers": drivers,
            "claimed": any(x in drivers for x in ("usbfs", "rtl2832_sdr", "airspy", "cdc_acm")),
            "is_hub": "hub" in drivers or vid_pid in HUB_NAMES or is_root,
            "is_root": is_root,
            "parent": _parent_of(name),
            "pci": pci,
            "pci_driver": _pci_driver(pci) if pci else "",
            "panel": _read(d / "physical_location" / "panel"),
        }
    return devices


def _pgrep_exact(name: str) -> list[int]:
    pids: list[int] = []
    proc = Path("/proc")
    if not proc.is_dir():
        return pids
    for ent in proc.iterdir():
        if not ent.name.isdigit():
            continue
        if _read(ent / "comm") == name:
            try:
                pids.append(int(ent.name))
            except ValueError:
                pass
    return pids


def _cmdline(pid: int) -> str:
    return _read(Path(f"/proc/{pid}/cmdline")).replace("\x00", " ")


def _radiod_pids(conf_substr: str | None = None) -> list[int]:
    pids = _pgrep_exact("radiod")
    if not conf_substr:
        return pids
    return [p for p in pids if conf_substr in _cmdline(p)]


def _hub_rates() -> dict[str, float]:
    try:
        req = urllib.request.Request(
            "http://127.0.0.1:8080/metrics",
            headers={"User-Agent": "wp-kmco-sdr/1.0"},
        )
        with urllib.request.urlopen(req, timeout=2.5) as r:
            text = r.read().decode("utf-8", errors="replace")
    except Exception:
        return {}
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


def _controller_for(dev: dict | None, devices: dict[str, dict]) -> dict:
    if not dev:
        return {}
    node = dev
    seen: set[str] = set()
    while node and node.get("sys") not in seen:
        seen.add(node.get("sys") or "")
        if node.get("is_root") and node.get("pci"):
            pci = node["pci"]
            meta = CONTROLLERS.get(pci, {})
            return {
                "pci": pci,
                "name": meta.get("name") or pci,
                "kind": meta.get("kind") or "other",
                "driver": node.get("pci_driver") or "",
                "card": meta.get("card") or "",
                "port": meta.get("port") or "",
            }
        parent = node.get("parent")
        node = devices.get(parent) if parent else None
    return {}


def _onboard_face(dev: dict, devices: dict[str, dict]) -> str:
    """Front/Rear for a device hanging off onboard Intel."""
    node: dict | None = dev
    first: dict | None = None
    seen: set[str] = set()
    while node and node.get("sys") not in seen:
        seen.add(node.get("sys") or "")
        sysname = node.get("sys") or ""
        if re.fullmatch(r"\d+-\d+", sysname):
            first = node
        if node.get("is_root"):
            break
        parent = node.get("parent")
        node = devices.get(parent) if parent else None
    panel = (first or {}).get("panel") or ""
    p = panel.lower()
    if p == "front":
        return "Front"
    # 5060 SFF: rear I/O shield is ACPI panel=top
    if p in ("rear", "top"):
        return "Rear"
    return panel.capitalize() if panel else ""


def _usb_path(dev: dict | None, devices: dict[str, dict]) -> str:
    """Human path, e.g. PCIe → StarTech USB Card A → Port 1
    or Onboard → Front. Hubs after that if the stick is not direct."""
    if not dev:
        return ""
    ctrl = _controller_for(dev, devices)
    hubs: list[str] = []
    node: dict | None = dev
    seen: set[str] = set()
    hops = 0
    while node and node.get("sys") not in seen:
        seen.add(node["sys"])
        if node.get("is_root"):
            break
        hops += 1
        if node.get("is_hub") and not node.get("is_root"):
            hubs.append(HUB_NAMES.get(node.get("vid_pid") or "") or "hub")
        parent = node.get("parent")
        node = devices.get(parent) if parent else None
    hubs.reverse()
    # Direct plug: no extra hops. Nested: one "hub" token, not every chip in the chain.
    if hops <= 1:
        hub_tail: list[str] = []
    else:
        hub_tail = ["hub"] if hubs else []

    kind = ctrl.get("kind")
    if kind == "renesas" and ctrl.get("card") and ctrl.get("port"):
        parts = ["PCIe", f"StarTech USB Card {ctrl['card']}", f"Port {ctrl['port']}"]
        parts.extend(hub_tail)
        return " → ".join(parts)
    if kind == "intel":
        hop = ""
        node2: dict | None = dev
        seen2: set[str] = set()
        while node2 and node2.get("sys") not in seen2:
            seen2.add(node2.get("sys") or "")
            sysname = node2.get("sys") or ""
            if re.fullmatch(r"\d+-\d+", sysname):
                hop = sysname
            if node2.get("is_root"):
                break
            parent = node2.get("parent")
            node2 = devices.get(parent) if parent else None
        jack = next((j for j in _onboard_jacks() if hop in j["sys"]), None)
        parts = ["Onboard"]
        if jack:
            parts.extend([jack["side"], jack["port"]])
        else:
            face = _onboard_face(dev, devices)
            if face:
                parts.append(face)
        parts.extend(hub_tail)
        return " → ".join(parts)
    name = ctrl.get("name") or ""
    if name:
        return " → ".join([name] + hub_tail)
    return " → ".join(hub_tail) if hub_tail else ""


def _want_vids(exp: dict) -> set[str]:
    vids = set()
    if exp.get("vid_pid"):
        vids.add(exp["vid_pid"])
    vids.update(exp.get("alt_vid_pids") or [])
    return vids


def match_expected(
    expected: list[dict], devices: dict[str, dict]
) -> dict[str, dict]:
    """Map expected-id → sysfs device. Serial, then path, then leftover VID:PID."""
    used: set[str] = set()
    found: dict[str, dict] = {}

    def take(dev: dict) -> dict:
        used.add(dev["sys"])
        return dev

    for exp in expected:
        serial = (exp.get("serial") or "").lower()
        if not serial:
            continue
        for dev in devices.values():
            if dev["sys"] in used or dev.get("is_root"):
                continue
            if (dev.get("serial") or "").lower() == serial:
                found[exp["id"]] = take(dev)
                break

    for exp in expected:
        if exp["id"] in found:
            continue
        hint = exp.get("path") or ""
        if not hint or hint not in devices:
            continue
        dev = devices[hint]
        if dev["sys"] in used:
            continue
        vids = _want_vids(exp)
        if vids and dev["vid_pid"] not in vids:
            continue
        found[exp["id"]] = take(dev)

    for exp in expected:
        if exp["id"] in found:
            continue
        vids = _want_vids(exp)
        if not vids:
            continue
        for dev in devices.values():
            if dev["sys"] in used or dev.get("is_root"):
                continue
            if dev["vid_pid"] in vids:
                found[exp["id"]] = take(dev)
                break
    return found


def _host_bits(devices: dict[str, dict]) -> dict[str, Any]:
    usbfs = _read(SYS_USBCORE / "usbfs_memory_mb") or "missing"
    autosuspend = _read(SYS_USBCORE / "autosuspend") or "missing"
    fw_ok = FW_PATH.is_file()
    intel_n = 0
    renesas_n = 0
    for dev in devices.values():
        if dev.get("is_root") or dev.get("is_hub"):
            continue
        kind = _controller_for(dev, devices).get("kind")
        if kind == "intel":
            intel_n += 1
        elif kind == "renesas":
            renesas_n += 1
    notes = []
    if usbfs != "0":
        notes.append(f"usbfs_memory_mb={usbfs} (want 0; Iridium drops samples at 16)")
    if autosuspend not in ("-1", "missing"):
        notes.append(f"usbcore.autosuspend={autosuspend} (want -1)")
    if not fw_ok:
        notes.append("renesas_usb_fw.mem missing under /lib/firmware")
    if intel_n and not renesas_n:
        notes.append("Radios on onboard Intel; StarTech cards empty")
    status = "issue" if usbfs != "0" or any("firmware" in n for n in notes) else "ok"
    return {
        "usbfs_memory_mb": usbfs,
        "autosuspend": autosuspend,
        "renesas_firmware": fw_ok,
        "live_on_intel": intel_n,
        "live_on_startech": renesas_n,
        "notes": notes,
        "status": status,
    }


def build_tree(devices: dict[str, dict], by_sys: dict[str, dict]) -> list[dict]:
    children: dict[str, list[str]] = {}
    roots: list[str] = []
    for name, dev in devices.items():
        parent = dev.get("parent")
        if not parent:
            roots.append(name)
        else:
            children.setdefault(parent, []).append(name)

    def sort_key(n: str) -> tuple:
        m = re.match(r"usb(\d+)$", n)
        if m:
            return (0, int(m.group(1)))
        m = re.match(r"(\d+)-([\d.]+)$", n)
        if m:
            return (1, int(m.group(1)), tuple(int(x) for x in m.group(2).split(".")))
        return (2, 0, n)

    def node_of(name: str) -> dict:
        dev = devices[name]
        role = by_sys.get(name)
        vid_pid = dev["vid_pid"]
        if role:
            kind = role.get("kind") or "device"
            label = f"{role['hardware']} · {role['role']}"
            status = role["status"]
            face = _onboard_face(dev, devices) if _controller_for(dev, devices).get("kind") == "intel" else ""
            parent = devices.get(dev.get("parent") or "")
            if face and parent and parent.get("is_root"):
                label = f"{face} · {label}"
        elif dev.get("is_root"):
            kind = "controller"
            pci = dev.get("pci") or ""
            meta = CONTROLLERS.get(pci, {})
            usb_gen = "USB 3" if vid_pid == "1d6b:0003" else "USB 2"
            label = f"{meta.get('name') or pci or name} · {usb_gen}"
            status = "empty" if not children.get(name) else "ok"
        elif dev.get("is_hub"):
            kind = "hub"
            label = HUB_NAMES.get(vid_pid) or dev.get("product") or "Hub"
            face = _onboard_face(dev, devices) if _controller_for(dev, devices).get("kind") == "intel" else ""
            parent = devices.get(dev.get("parent") or "")
            if face and parent and parent.get("is_root"):
                label = f"{face} · {label}"
            status = "ok"
        else:
            kind = "device"
            label = dev.get("product") or vid_pid
            face = _onboard_face(dev, devices) if _controller_for(dev, devices).get("kind") == "intel" else ""
            parent = devices.get(dev.get("parent") or "")
            if face and parent and parent.get("is_root"):
                label = f"{face} · {label}"
            status = "ok"
        return {
            "id": name,
            "kind": kind,
            "label": label,
            "vid_pid": vid_pid,
            "serial": dev.get("serial") or "",
            "speed": _speed_label(dev.get("speed") or ""),
            "bus": dev.get("bus"),
            "dev": dev.get("dev"),
            "drivers": dev.get("drivers") or [],
            "pci": dev.get("pci") or "",
            "status": status,
            "role": role["id"] if role else None,
            "children": [node_of(c) for c in sorted(children.get(name, []), key=sort_key)],
        }

    by_pci: dict[str, list[str]] = {}
    orphans: list[str] = []
    for r in sorted(roots, key=sort_key):
        pci = devices[r].get("pci") or ""
        if pci:
            by_pci.setdefault(pci, []).append(r)
        else:
            orphans.append(r)

    tree: list[dict] = []
    pci_order = list(CONTROLLERS.keys()) + [p for p in by_pci if p not in CONTROLLERS]
    for pci in pci_order:
        buses = by_pci.get(pci) or []
        if not buses:
            continue
        meta = CONTROLLERS.get(pci, {})
        kids = [node_of(b) for b in buses]

        def count_leaves(n: dict) -> int:
            extra = 1 if n["kind"] in ("radio", "mesh", "gps", "device") else 0
            return extra + sum(count_leaves(c) for c in n["children"])

        leaf_count = sum(count_leaves(k) for k in kids)
        status = "ok" if leaf_count else ("empty" if meta.get("kind") == "renesas" else "ok")
        tree.append(
            {
                "id": pci,
                "kind": "host",
                "label": meta.get("name") or pci,
                "vid_pid": "",
                "serial": pci,
                "speed": "",
                "pci": pci,
                "card": meta.get("card") or "",
                "port": meta.get("port") or "",
                "driver": devices[buses[0]].get("pci_driver") or "",
                "status": status,
                "note": meta.get("note") or "",
                "device_count": leaf_count,
                "children": kids,
            }
        )
    for o in orphans:
        tree.append(node_of(o))
    return tree


def _strip_face(label: str) -> str:
    return re.sub(r"^(Front|Rear)\s+·\s+", "", label or "")


def _face_from_panel(panel: str) -> str:
    p = (panel or "").lower()
    if p == "front":
        return "Front"
    if p in ("rear", "top"):
        return "Rear"
    return panel.capitalize() if panel else "—"


def _empty_row(group: str, side: str, port: str) -> dict:
    return {
        "group": group,
        "side": side,
        "port": port,
        "hub": "—",
        "device": "empty",
        "status": "empty",
        "serial": "",
        "speed": "",
    }


# OptiPlex 5060 SFF: 4 front (Type-C + 3× Type-A) and 6 rear (4× USB3, 2× USB2).
# ACPI only tags Type-C as panel=front; the other front jacks say "top" like the rear.
# Front cluster = usb1 ports 1/2/3/5. Rear = remaining hotplug USB3 pairs + USB2.
ONBOARD_JACK_MAP = {
    "0x80000002": ("Front", "USB 3 Type-C"),
    "0x80000001": ("Front", "USB 3 Type-A"),
    "0x80000003": ("Front", "USB 2 · 1"),
    "0x80000005": ("Front", "USB 2 · 2"),
    "0x80000006": ("Rear", "USB 3 · 1"),
    "0x80000008": ("Rear", "USB 3 · 2"),
    "0x8000000a": ("Rear", "USB 3 · 3"),
    "0x8000000c": ("Rear", "USB 3 · 4"),
    "0x80000009": ("Rear", "USB 2 · 1"),
    "0x8000000b": ("Rear", "USB 2 · 2"),
}
ONBOARD_JACK_ORDER = list(ONBOARD_JACK_MAP.keys())


def _onboard_jacks() -> list[dict]:
    """Physical onboard jacks: hotplug only, HS+SS companions merged by ACPI location."""
    xhci = Path("/sys/bus/pci/devices/0000:00:14.0")
    if not xhci.is_dir():
        return []
    by_loc: dict[str, dict] = {}
    for port in xhci.glob("usb?/?-0:1.0/usb?-port*"):
        if _read(port / "connect_type") != "hotplug":
            continue
        loc = (_read(port / "location") or "").lower()
        if not loc or loc in ("0x80000000", "0x0", "0"):
            continue
        m = re.match(r"usb(\d+)-port(\d+)$", port.name)
        if not m:
            continue
        sysname = f"{m.group(1)}-{m.group(2)}"
        rec = by_loc.setdefault(loc, {"loc": loc, "sys": []})
        rec["sys"].append(sysname)
    out: list[dict] = []
    seen: set[str] = set()
    for loc in ONBOARD_JACK_ORDER:
        key = loc.lower()
        side, port = ONBOARD_JACK_MAP[loc]
        rec = by_loc.get(key, {"loc": key, "sys": []})
        out.append({"loc": key, "side": side, "port": port, "sys": rec.get("sys") or []})
        seen.add(key)
    extra = 1
    for loc, rec in sorted(by_loc.items()):
        if loc in seen:
            continue
        out.append({"loc": loc, "side": "Rear", "port": f"extra {extra}", "sys": rec["sys"]})
        extra += 1
    return out


def flatten_tree_rows(tree: list[dict]) -> list[dict]:
    """One row per physical jack (empty if nothing plugged in). Extra rows
    when a hub on that jack has more than one radio."""
    occupied: dict[tuple[str, str], list[dict]] = {}

    def add(group: str, port: str, row: dict) -> None:
        occupied.setdefault((group, port), []).append(row)

    for host in tree:
        if host.get("kind") != "host":
            continue
        card = host.get("card") or ""
        port_n = host.get("port") or ""
        label = host.get("label") or ""
        if card:
            group = f"StarTech USB Card {card}"
            port_fixed = f"Port {port_n}"
        elif "Onboard" in label:
            group = "Onboard"
            port_fixed = ""
        else:
            continue

        def payload_kind(n: dict) -> bool:
            if n.get("kind") in ("radio", "mesh", "gps", "device"):
                return True
            return any(payload_kind(c) for c in (n.get("children") or []))

        def walk(n: dict, via_hub: bool, hop: str) -> None:
            kind = n.get("kind") or ""
            lab = n.get("label") or ""
            sysid = n.get("id") or ""
            hop2 = hop
            if re.fullmatch(r"[12]-\d+", sysid):
                hop2 = sysid
            via2 = via_hub or kind == "hub"
            if kind in ("radio", "mesh", "gps", "device"):
                if card:
                    g, p = group, port_fixed
                else:
                    g, p = "Onboard", hop2 or "—"
                add(
                    g,
                    p,
                    {
                        "group": g,
                        "side": "",
                        "port": p,
                        "hub": "hub" if via2 else "—",
                        "device": _strip_face(lab),
                        "status": n.get("status") or "ok",
                        "serial": n.get("serial") or "",
                        "speed": n.get("speed") or "",
                    },
                )
                return
            for c in n.get("children") or []:
                walk(c, via2, hop2)
            # Hub plugged in a jack with nothing behind it still occupies the port.
            if (
                kind == "hub"
                and hop2
                and re.fullmatch(r"[12]-\d+", sysid)
                and not payload_kind(n)
            ):
                g, p = ("Onboard", hop2) if not card else (group, port_fixed)
                add(
                    g,
                    p,
                    {
                        "group": g,
                        "side": "",
                        "port": p,
                        "hub": "hub",
                        "device": _strip_face(lab) or "hub",
                        "status": "ok",
                        "serial": "",
                        "speed": n.get("speed") or "",
                    },
                )

        walk(host, False, "")

    rows: list[dict] = []
    # Onboard: every hotplug jack, Front then Rear.
    hop_to_jack: dict[str, tuple[str, str]] = {}
    for jack in _onboard_jacks():
        key = ("Onboard", jack["port"])
        for sysname in jack["sys"]:
            hop_to_jack[sysname] = key
        hits = []
        for hop, recs in list(occupied.items()):
            if hop[0] != "Onboard":
                continue
            if hop[1] in jack["sys"]:
                for r in recs:
                    r["port"] = jack["port"]
                    hits.append(r)
        payload = [h for h in hits if "hub" not in (h.get("device") or "").lower()]
        side = jack["side"]
        if payload:
            for h in payload:
                h["side"] = side
                h["port"] = jack["port"]
            rows.extend(payload)
        elif hits:
            rows.append(
                {
                    "group": "Onboard",
                    "side": side,
                    "port": jack["port"],
                    "hub": "hub",
                    "device": "hub",
                    "status": "ok",
                    "serial": "",
                    "speed": "",
                }
            )
        else:
            rows.append(_empty_row("Onboard", side, jack["port"]))

    # StarTech: every card / port (one physical jack per Renesas chip).
    for pci, meta in CONTROLLERS.items():
        if meta.get("kind") != "renesas":
            continue
        group = f"StarTech USB Card {meta['card']}"
        port = f"Port {meta['port']}"
        hits = occupied.get((group, port))
        if hits:
            for h in hits:
                h["side"] = "—"
            rows.extend(hits)
        else:
            rows.append(_empty_row(group, "—", port))
    return rows


def collect_sdr(ctr_map: dict[str, dict] | None = None) -> dict:
    t0 = time.time()
    expected, source = load_expected()
    devices = scan_usb()
    matched = match_expected(expected, devices)
    rates = _hub_rates()
    ctrs = ctr_map or {}

    items: list[dict] = []
    by_sys: dict[str, dict] = {}

    for exp in expected:
        dev = matched.get(exp["id"])
        present = dev is not None
        decoder = exp.get("decoder") or ""
        docker_name = decoder if decoder and decoder not in HOST_DECODERS else None
        cinfo = ctrs.get(docker_name) if docker_name else None
        extra: list[str] = []
        if exp.get("notes"):
            extra.append(exp["notes"])

        radiod_pids = _radiod_pids(RADIOD_CONF.get(decoder)) if decoder in RADIOD_CONF else []
        if decoder == "radiod":
            extra.append(f"radiod pid {radiod_pids[0]}" if radiod_pids else "radiod not running")
            extra.append(f"{len(_pgrep_exact('dumphfdl'))}× dumphfdl")
        elif decoder == "vhf-r2":
            extra.append(f"radiod pid {radiod_pids[0]}" if radiod_pids else "radiod not running")
            extra.append(f"{len(_pgrep_exact('acarsdec'))}× acarsdec")
            extra.append("dumpvdl2 up" if _pgrep_exact("dumpvdl2") else "dumpvdl2 not running")
        elif decoder == "gpsd":
            extra.append("gpsd active" if _pgrep_exact("gpsd") else "gpsd not running")
        elif docker_name and cinfo:
            extra.append(cinfo.get("status") or cinfo.get("state") or "")
        elif docker_name:
            extra.append("container not deployed")

        if exp.get("no_data"):
            st = "off"
            extra.append("does not enumerate on USB")
        elif exp.get("parked"):
            if present:
                st = "ok"
                extra.append("parked spare is plugged in")
            elif _blank(exp.get("serial")) and not exp.get("vid_pid"):
                st = "off"
                extra.append("no serial yet")
            else:
                st = "off"
                extra.append("not on USB (expected)")
        elif not present:
            if _blank(exp.get("serial")) and not exp.get("vid_pid") and not exp.get("path"):
                st = "off"
                extra.append("not identifiable yet")
            else:
                st = "down"
                extra.append("not on USB")
        elif decoder in RADIOD_CONF:
            st = "ok" if radiod_pids else "issue"
        elif decoder == "gpsd":
            st = "ok" if _pgrep_exact("gpsd") else "issue"
        elif decoder == "meshtastic":
            st = "ok"
        elif docker_name and (not cinfo or cinfo.get("state") == "missing"):
            st = "issue"
        elif docker_name and not cinfo.get("running"):
            st = "down"
            extra.append(cinfo.get("status") or "container not running")
        elif docker_name and cinfo.get("health") == "unhealthy":
            st = "issue"
        elif exp["kind"] == "radio" and not dev.get("claimed"):
            # auto_rx only holds the RTL during each rtl_fm / rtl_power hop.
            if decoder == "radiosonde_auto_rx" and docker_name and cinfo and cinfo.get("running"):
                st = "ok"
                extra.append("scan duty-cycle (usbfs only while rtl_fm/rtl_power runs)")
            else:
                st = "issue"
                extra.append("on USB but not claimed (usbfs)")
        else:
            st = "ok"

        if decoder == "vhf-r2":
            ac = rates.get("acarshub_rrd_acars_messages_per_minute")
            vdl = rates.get("acarshub_rrd_vdlm_messages_per_minute")
            bits = []
            if ac is not None:
                bits.append(f"{ac:.0f}/min ACARS")
            if vdl is not None:
                bits.append(f"{vdl:.0f}/min VDL2")
            rate_s = " · ".join(bits) if bits else None
            rpm = (ac or 0) + (vdl or 0) if (ac is not None or vdl is not None) else None
        else:
            rpm = rates.get(RATE_BY_DECODER[decoder]) if decoder in RATE_BY_DECODER else None
            rate_s = f"{rpm:.0f}/min" if rpm is not None else None
        if rpm == 0 and st == "ok":
            extra.append("0 msgs/min (quiet or decoder idle)")

        ctrl = _controller_for(dev, devices)
        row = {
            "id": exp["id"],
            "kind": exp["kind"],
            "role": exp["role"],
            "mode": exp["role"],
            "hardware": exp["hardware"],
            "serial": exp.get("serial") or (dev or {}).get("serial") or "",
            "vid_pid": (dev or {}).get("vid_pid") or exp.get("vid_pid") or "",
            "container": decoder or None,
            "notes": exp.get("notes") or "",
            "parked": bool(exp.get("parked")),
            "status": st,
            "present": present,
            "claimed": bool(dev and dev.get("claimed")),
            "detail": "; ".join(p for p in extra if p),
            "rate": rate_s,
            "usb": {
                "sys": dev.get("sys"),
                "bus": dev.get("bus"),
                "dev": dev.get("dev"),
                "speed": _speed_label(dev.get("speed") or ""),
                "drivers": dev.get("drivers") or [],
                "path": _usb_path(dev, devices),
                "controller": ctrl.get("name"),
                "pci": ctrl.get("pci"),
            }
            if present
            else None,
        }
        items.append(row)
        if present and dev:
            by_sys[dev["sys"]] = row

    unknown = []
    for name, dev in devices.items():
        if dev.get("is_root"):
            continue
        if name in by_sys:
            continue
        unknown.append(
            {
                "sys": name,
                "vid_pid": dev["vid_pid"],
                "product": dev.get("product"),
                "serial": dev.get("serial"),
                "path": _usb_path(dev, devices),
                "is_hub": bool(dev.get("is_hub")),
            }
        )

    counts = {"ok": 0, "quiet": 0, "issue": 0, "down": 0, "unknown": 0, "off": 0}
    for it in items:
        counts[it["status"]] = counts.get(it["status"], 0) + 1

    tree = build_tree(devices, by_sys)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "station": "WP-KMCO / WinterPark",
        "elapsed_ms": int((time.time() - t0) * 1000),
        "source": source,
        "summary": counts,
        "kind_order": KIND_ORDER,
        "kind_title": KIND_TITLE,
        "host": _host_bits(devices),
        "items": items,
        "radios": [i for i in items if i["kind"] == "radio"],
        "unknown": unknown,
        "tree": tree,
        "tree_rows": flatten_tree_rows(tree),
        "usb_device_count": sum(
            1 for d in devices.values() if not d.get("is_root") and not d.get("is_hub")
        ),
    }
