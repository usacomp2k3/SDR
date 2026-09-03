#!/usr/bin/env python3
"""Inject libacars FANS (ADS-C / CPDLC) into acarsdec JSON before acars_router.

acarsdec is built with libacars but VHF FANS was arriving at ACARS Hub as hex
only (empty libacars column). This sits where acars-bridge used to: UDP in
from acarsdec, UDP out to the router.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import socket
import sys
from ctypes import (
    CDLL,
    POINTER,
    Structure,
    c_bool,
    c_char_p,
    c_int,
    c_size_t,
    c_void_p,
)

LA_MSG_DIR_UNKNOWN = 0
LA_MSG_DIR_GND2AIR = 1
LA_MSG_DIR_AIR2GND = 2

MD_RE = re.compile(r"^(?:- )?#M[A-Z0-9]/([A-Z0-9]{2})\s+(.*)", re.S | re.I)
FANS_MARK = (".ADS.", ".AT1.", ".AFN.", "#MD/A6", "#MD/AA", "#MD/A0")
FANS_LABELS = {"B6", "A6", "AA", "A0", "H1"}


class la_vstr(Structure):
    _fields_ = [
        ("str", c_char_p),
        ("len", c_size_t),
        ("allocated_size", c_size_t),
    ]


class la_type_descriptor(Structure):
    _fields_ = [
        ("format_text", c_void_p),
        ("destroy", c_void_p),
        ("format_json", c_void_p),
        ("json_key", c_char_p),
    ]


class la_proto_node(Structure):
    pass


la_proto_node._fields_ = [
    ("td", POINTER(la_type_descriptor)),
    ("data", c_void_p),
    ("next", POINTER(la_proto_node)),
]


def load_lib():
    lib = CDLL("libacars-2.so")
    lib.la_acars_decode_apps.restype = POINTER(la_proto_node)
    lib.la_acars_decode_apps.argtypes = [c_char_p, c_char_p, c_int]
    lib.la_proto_tree_format_json.restype = POINTER(la_vstr)
    lib.la_proto_tree_format_json.argtypes = [c_void_p, POINTER(la_proto_node)]
    lib.la_vstring_destroy.restype = None
    lib.la_vstring_destroy.argtypes = [POINTER(la_vstr), c_bool]
    lib.la_proto_tree_destroy.restype = None
    lib.la_proto_tree_destroy.argtypes = [POINTER(la_proto_node)]
    return lib


def decode_apps(lib, label: str, text: str, direction: int):
    if not label or not text:
        return None
    node = lib.la_acars_decode_apps(label.encode("ascii", "replace"), text.encode("latin-1", "replace"), direction)
    if not node:
        return None
    try:
        vstr = lib.la_proto_tree_format_json(None, node)
        if not vstr or not vstr.contents.str:
            return None
        raw = vstr.contents.str.decode("ascii", "replace")
        lib.la_vstring_destroy(vstr, True)
    finally:
        lib.la_proto_tree_destroy(node)
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return obj if useful(obj) else None


def useful(obj) -> bool:
    blob = json.dumps(obj, separators=(",", ":"))
    if any(k in blob for k in ('"basic_report"', '"wpt_change_event"', '"predicted_route"', '"cpdlc"', '"fans1a"')):
        return True
    if '"arinc622"' in blob and '"msg_type"' in blob:
        # contract req / disconnect still count
        if '"tags":[]' in blob and '"err":true' in blob:
            return False
        return True
    return False


def already_decoded(msg: dict) -> bool:
    la = msg.get("libacars")
    if not la:
        return False
    if isinstance(la, str):
        try:
            la = json.loads(la)
        except json.JSONDecodeError:
            return bool(la.strip())
    return useful(la) if isinstance(la, dict) else True


def looks_like_fans(label: str, text: str) -> bool:
    if label in FANS_LABELS:
        return True
    up = text.upper()
    return any(m in up for m in FANS_MARK)


def direction_of(msg: dict) -> int:
    bid = str(msg.get("block_id") or "")
    if bid and bid[0].isdigit():
        return LA_MSG_DIR_AIR2GND
    return LA_MSG_DIR_GND2AIR


def slash(text: str) -> str:
    t = text.lstrip()
    return t if t.startswith("/") else "/" + t


def candidates(label: str, text: str, sublabel: str | None):
    seen = set()

    def add(lab, txt):
        key = (lab, txt)
        if lab and txt and key not in seen:
            seen.add(key)
            yield lab, txt

    yield from add(label, text)
    if sublabel:
        yield from add(sublabel, text)
        yield from add(sublabel, slash(text))
    m = MD_RE.match(text)
    if m:
        yield from add(m.group(1).upper(), m.group(2))
        yield from add(m.group(1).upper(), slash(m.group(2)))
    up = text.upper()
    if ".ADS." in up:
        yield from add("B6", slash(text))
        yield from add("A6", slash(text))
        yield from add("B6", text)
        yield from add("A6", text)
    if ".AT1." in up or ".AFN." in up:
        yield from add("AA", slash(text))
        yield from add("A0", slash(text))
        yield from add("AA", text)


def enrich(lib, msg: dict) -> dict:
    if already_decoded(msg):
        return msg
    text = msg.get("text") or msg.get("msg_text") or ""
    label = (msg.get("label") or "").replace("\x7f", "d")
    if not looks_like_fans(label, text):
        return msg
    sub = msg.get("sublabel") or None
    dirs = [direction_of(msg)]
    other = LA_MSG_DIR_GND2AIR if dirs[0] == LA_MSG_DIR_AIR2GND else LA_MSG_DIR_AIR2GND
    dirs.append(other)
    for lab, txt in candidates(label, text, sub):
        for d in dirs:
            obj = decode_apps(lib, lab, txt, d)
            if obj:
                msg["libacars"] = obj
                return msg
    return msg


def parse_hostport(s: str):
    host, port = s.rsplit(":", 1)
    return host, int(port)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--listen", default="0.0.0.0:5550")
    p.add_argument("--forward", default="acars_router:5550")
    args = p.parse_args()
    lib = load_lib()
    lhost, lport = parse_hostport(args.listen)
    fhost, fport = parse_hostport(args.forward)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((lhost, lport))
    dest_ip = None
    decoded = 0
    print(f"libacars-enrich listen {lhost}:{lport} -> {fhost}:{fport}", flush=True)

    def dest():
        nonlocal dest_ip
        if dest_ip is None:
            dest_ip = socket.getaddrinfo(fhost, fport, socket.AF_INET, socket.SOCK_DGRAM)[0][4][0]
            print(f"resolved {fhost} -> {dest_ip}", flush=True)
        return (dest_ip, fport)

    while True:
        data, _ = sock.recvfrom(65535)
        line = data.strip()
        try:
            target = dest()
        except socket.gaierror as e:
            dest_ip = None
            print(f"forward resolve failed ({e}); drop 1 pkt", flush=True)
            continue
        if not line.startswith(b"{"):
            sock.sendto(data, target)
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            sock.sendto(data, target)
            continue
        if not isinstance(msg, dict):
            sock.sendto(data, target)
            continue
        before = "libacars" in msg
        msg = enrich(lib, msg)
        if not before and "libacars" in msg:
            decoded += 1
            if decoded <= 20 or decoded % 50 == 0:
                ac = msg["libacars"]
                print(
                    f"decoded #{decoded} label={msg.get('label')} tail={msg.get('tail')} keys={list(ac)[:6]}",
                    flush=True,
                )
        out = json.dumps(msg, separators=(",", ":")).encode("utf-8")
        sock.sendto(out, target)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        sys.exit(0)
