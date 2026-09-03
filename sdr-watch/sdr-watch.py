#!/usr/bin/env python3
"""Unattended SDR recovery for WP-KMCO.

One process at a time (flock). Reads expected radios from Equipmentlist.md.
If a live radio is on USB but its decoder is dead, unclaimed, or (Iridium)
stuck at 100% sample loss after a yank/replug, restart only that decoder.

Covers every unplug/replug mix: one stick, a whole hub, all radios at once,
RX888 DFU then SuperSpeed, brief Airspy blips. Unplugged radios are left
alone. Parked / unused / USB-power-only / mesh / GPS are ignored.

RX888 recovery is rx888/health-fix.sh (systemd radiod + pcmrecord).
Do not run the user timer — system timer + udev only.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import re
import subprocess
import sys
import time
from pathlib import Path

_HUB = str(Path(__file__).resolve().parents[1])
ROOT = Path(os.environ.get("SDR_WATCH_ROOT", _HUB))
EQUIPMENT = Path(os.environ.get("SDR_EQUIPMENT_LIST", ROOT / "Equipmentlist.md"))
STATE = Path(os.environ.get("SDR_WATCH_STATE", "/run/sdr-watch"))
SYS_USB = Path("/sys/bus/usb/devices")
HEALTH_FIX = ROOT / "rx888" / "health-fix.sh"
RX888_APP = "04b4:00f1"
RX888_DFU = "04b4:00f3"

# Seconds a docker decoder must be up before we call it wedged.
GRACE_S = 60
# After a *successful* restart, do not bounce the same decoder again.
SUCCESS_COOLDOWN_S = 120
# After 100% Iridium loss with usbfs claimed: USB bandwidth, not a stale handle.
BANDWIDTH_BACKOFF_S = 900

DOCKER_DECODERS = {
    "irdm",
    "airspy_adsb",
    "dump978",
    "shipfeeder",
    "radiosonde_auto_rx",
}


def log(msg: str) -> None:
    print(f"sdr-watch: {msg}", flush=True)


def _read(path: Path) -> str:
    try:
        return path.read_text().strip()
    except Exception:
        return ""


def _blank(s: str | None) -> bool:
    v = (s or "").strip()
    return v == "" or v.upper() in ("TBD", "?", "NONE", "—", "-")


def _norm_serial(raw: str) -> str:
    s = (raw or "").strip()
    if s.upper().startswith("AIRSPY SN:"):
        s = s.split(":", 1)[1]
    return s.strip()


def parse_equipment_usb(text: str) -> list[dict]:
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^##\s+USB devices\b", line, re.I):
            start = i + 1
            break
    if start is None:
        return []
    header = None
    rows = []
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
        notes = rec.get("notes") or ""
        alt = re.findall(r"\b([0-9a-f]{4}:[0-9a-f]{4})\b", notes, re.I)
        serial = rec.get("serial") or ""
        rows.append(
            {
                "role": rec.get("role") or "",
                "hardware": rec.get("hardware") or "",
                "serial": "" if _blank(serial) else serial,
                "vid_pid": (rec.get("vid:pid") or "").lower(),
                "alt_vid_pids": [a.lower() for a in alt],
                "kind": (rec.get("kind") or "").lower(),
                "decoder": (rec.get("decoder") or "").strip(),
            }
        )
    return rows


def scan_usb() -> list[dict]:
    out = []
    if not SYS_USB.is_dir():
        return out
    for d in SYS_USB.iterdir():
        if ":" in d.name or d.name.startswith("usb"):
            continue
        vid, pid = _read(d / "idVendor"), _read(d / "idProduct")
        if not vid:
            continue
        drivers = []
        try:
            for iface in d.iterdir():
                if ":" not in iface.name:
                    continue
                link = iface / "driver"
                if link.exists() or link.is_symlink():
                    try:
                        drivers.append(os.path.basename(os.readlink(link)))
                    except OSError:
                        pass
        except OSError:
            pass
        out.append(
            {
                "sys": d.name,
                "vid_pid": f"{vid}:{pid}",
                "serial": _norm_serial(_read(d / "serial")),
                "drivers": drivers,
                "claimed": any(x in drivers for x in ("usbfs", "rtl2832_sdr", "airspy")),
            }
        )
    return out


def match_dev(exp: dict, devices: list[dict]) -> dict | None:
    serial = (exp.get("serial") or "").lower()
    vids = {exp["vid_pid"]} if exp.get("vid_pid") else set()
    vids.update(exp.get("alt_vid_pids") or [])
    if serial:
        for d in devices:
            if (d.get("serial") or "").lower() == serial:
                return d
    if exp["decoder"] == "radiod":
        for d in devices:
            if d["vid_pid"] in vids:
                return d
    return None


def sh(args: list[str], timeout: float = 20) -> subprocess.CompletedProcess:
    return subprocess.run(
        args, capture_output=True, text=True, timeout=timeout, check=False
    )


def docker_state(name: str) -> dict:
    r = sh(
        [
            "docker",
            "inspect",
            "-f",
            "{{.State.Running}} {{.State.StartedAt}} {{.State.Status}}",
            name,
        ]
    )
    if r.returncode != 0:
        return {"exists": False, "running": False, "age_s": 0, "status": "missing"}
    parts = (r.stdout or "").split()
    running = parts and parts[0].lower() == "true"
    started = parts[1] if len(parts) > 1 else ""
    status = parts[2] if len(parts) > 2 else ""
    age = 0
    if started:
        try:
            from datetime import datetime, timezone

            ts = started.replace("Z", "+00:00")
            if "." in ts:
                head, rest = ts.split(".", 1)
                frac, tz = rest.split("+", 1) if "+" in rest else rest.split("-", 1)
                ts = f"{head}.{frac[:6]}+{tz}" if "+" in rest else f"{head}.{frac[:6]}-{tz}"
            started_dt = datetime.fromisoformat(ts)
            if started_dt.tzinfo is None:
                started_dt = started_dt.replace(tzinfo=timezone.utc)
            age = max(0, int((datetime.now(timezone.utc) - started_dt).total_seconds()))
        except Exception:
            age = 9999
    return {"exists": True, "running": running, "age_s": age, "status": status}


def _state_dir() -> Path:
    try:
        STATE.mkdir(parents=True, exist_ok=True)
        probe = STATE / ".ok"
        probe.write_text("1")
        return STATE
    except OSError:
        fallback = Path(f"/tmp/sdr-watch-{os.getuid()}")
        fallback.mkdir(parents=True, exist_ok=True)
        return fallback


def cooldown_ok(key: str, seconds: int) -> bool:
    p = _state_dir() / f"{key}.stamp"
    try:
        if time.time() - p.stat().st_mtime < seconds:
            return False
    except FileNotFoundError:
        pass
    return True


def mark(key: str) -> None:
    (_state_dir() / f"{key}.stamp").write_text(str(int(time.time())))


def irdm_wedged() -> bool:
    r = sh(["docker", "logs", "--since", "25s", "irdm"], timeout=15)
    text = (r.stdout or "") + (r.stderr or "")
    return text.count("100%") >= 8


def pgrep_x(name: str) -> bool:
    return sh(["pgrep", "-x", name]).returncode == 0


def dumphfdl_n() -> int:
    r = sh(["pgrep", "-c", "-x", "dumphfdl"])
    try:
        return int((r.stdout or "0").strip() or "0")
    except ValueError:
        return 0


def hf_healthy() -> bool:
    return pgrep_x("radiod") and pgrep_x("pcmrecord") and dumphfdl_n() >= 12


def vhf_radiod_up() -> bool:
    return sh(["pgrep", "-f", r"radiod .*vhf-r2/radiod.conf"]).returncode == 0


def vhf_healthy() -> bool:
    d = sh(["pgrep", "-f", r"dumpvdl2 --iq-file"])
    a = sh(["pgrep", "-f", r"acarsdec -i WP-KMCO-ACARS"])
    return vhf_radiod_up() and d.returncode == 0 and a.returncode == 0


def act(dry: bool, decoder: str, reason: str, cmd: list[str], timeout: float = 60) -> bool:
    """Restart one decoder. Stamp cooldown only when the command succeeds."""
    if not cooldown_ok(decoder, SUCCESS_COOLDOWN_S):
        log(f"{decoder}: skip (cooldown) — {reason}")
        return False
    log(f"{decoder}: {reason} — {'DRY ' if dry else ''}{' '.join(cmd)}")
    if dry:
        return True
    try:
        r = sh(cmd, timeout=timeout)
    except subprocess.TimeoutExpired:
        log(f"{decoder}: command timed out ({int(timeout)}s) — will retry next pass")
        return False
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip().splitlines()
        log(f"{decoder}: command failed: {err[-1] if err else r.returncode}")
        return False
    mark(decoder)
    return True


def check_one(exp: dict, devices: list[dict], dry: bool) -> None:
    kind = exp.get("kind") or ""
    decoder = exp.get("decoder") or ""
    if kind in ("hub", "amp", "unused", "mesh", "gps") or not decoder:
        return
    if decoder not in DOCKER_DECODERS and decoder not in ("radiod", "vhf-r2"):
        return

    label = f"{exp.get('hardware')} ({exp.get('role')})"
    dev = match_dev(exp, devices)

    if decoder == "radiod":
        if not dev:
            return
        if dev.get("vid_pid") == RX888_DFU:
            log(f"{decoder}: {label} in FX3 bootloader — wait for 00f1")
            return
        if hf_healthy():
            return
        if not HEALTH_FIX.is_file():
            log(f"{decoder}: health-fix.sh missing")
            return
        why = f"{label} on USB, HF farm down (radiod={int(pgrep_x('radiod'))} pcmrecord={int(pgrep_x('pcmrecord'))} dumphfdl={dumphfdl_n()})"
        if act(dry, "radiod", why, ["bash", str(HEALTH_FIX)], timeout=150):
            if not dry and not hf_healthy():
                log("radiod: health-fix returned but farm not up yet — next pass will retry")
        return

    if decoder == "vhf-r2":
        if not dev:
            return
        if vhf_healthy():
            return
        # Decode-only if radiod still holds the R2 — bouncing radiod drops VDL2/ACARS.
        if vhf_radiod_up():
            act(
                dry,
                decoder,
                f"{label} on USB, VHF decode down (radiod up)",
                ["systemctl", "restart", "vhf-r2-decode.service"],
                timeout=90,
            )
        else:
            act(
                dry,
                decoder,
                f"{label} on USB, VHF mux down",
                ["systemctl", "restart", "vhf-r2-radiod.service", "vhf-r2-decode.service"],
                timeout=90,
            )
        return

    st = docker_state(decoder)
    if not st["exists"]:
        return

    if not dev:
        # Unplugged — do not restart-loop an unhealthy container.
        return

    if not st["running"]:
        act(
            dry,
            decoder,
            f"{label} on USB, container {st['status']}",
            ["docker", "start", decoder],
        )
        return

    if st["age_s"] < GRACE_S:
        return

    if decoder == "irdm" and irdm_wedged():
        if dev.get("claimed"):
            if not cooldown_ok("irdm-bw", BANDWIDTH_BACKOFF_S):
                log("irdm: 100% sample loss but usbfs claimed — USB bandwidth, backing off")
                return
            mark("irdm-bw")
            act(
                dry,
                decoder,
                f"{label} 100% sample loss (one retry, then backoff)",
                ["docker", "restart", decoder],
            )
        else:
            act(
                dry,
                decoder,
                f"{label} 100% sample loss, no usbfs (stale handle)",
                ["docker", "restart", decoder],
            )
        return

    if not dev.get("claimed"):
        # auto_rx opens the RTL only for each rtl_fm / rtl_power hop.
        if decoder == "radiosonde_auto_rx":
            return
        act(
            dry,
            decoder,
            f"{label} on {dev['sys']} but not claimed by usbfs",
            ["docker", "restart", decoder],
        )


def acquire_lock() -> int | None:
    d = _state_dir()
    path = d / "sdr-watch.lock"
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        os.close(fd)
        return None
    return fd


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    lock_fd = acquire_lock()
    if lock_fd is None:
        log("another sdr-watch is running — skip")
        return 0

    if not EQUIPMENT.is_file():
        log(f"no Equipmentlist at {EQUIPMENT}")
        return 1
    expected = [
        r
        for r in parse_equipment_usb(EQUIPMENT.read_text(encoding="utf-8", errors="replace"))
        if r.get("kind") == "radio" or r.get("decoder") in DOCKER_DECODERS or r.get("decoder") in ("radiod", "vhf-r2")
    ]
    if not expected:
        log("no radio rows in Equipmentlist USB table")
        return 1
    devices = scan_usb()
    for exp in expected:
        try:
            check_one(exp, devices, args.dry_run)
        except subprocess.TimeoutExpired:
            log(f"{exp.get('decoder')}: command timed out")
        except Exception as e:
            log(f"{exp.get('decoder')}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
