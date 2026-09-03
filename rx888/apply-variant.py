#!/usr/bin/env python3
"""Rewrite radiod-hfdl.conf RX888 + channel AGC knobs for A/B runs."""
from __future__ import annotations

import argparse
import pathlib
import re
import sys

_HUB = str(pathlib.Path(__file__).resolve().parents[1])
CONF = pathlib.Path(_HUB + "/rx888/run/radiod-hfdl.conf")

VARIANTS = {
    "baseline": {
        "dither": "yes",
        "rand": None,
        "att": None,
        "gain": None,
        "queuedepth": "16",
        "chan_agc": "yes",
        "hang": "0",
        "recovery": "100",
    },
    "agc_off": {
        "dither": "yes",
        "rand": None,
        "att": None,
        "gain": None,
        "queuedepth": "16",
        "chan_agc": "no",
        "hang": "0",
        "recovery": "100",
    },
    "agc_slow": {
        "dither": "yes",
        "rand": None,
        "att": None,
        "gain": None,
        "queuedepth": "16",
        "chan_agc": "yes",
        "hang": "2",
        "recovery": "10",
    },
    "gain25": {
        "dither": "yes",
        "rand": None,
        "att": "0",
        "gain": "25",
        "queuedepth": "16",
        "chan_agc": "no",
        "hang": "0",
        "recovery": "100",
    },
    "gain18_att6": {
        "dither": "yes",
        "rand": None,
        "att": "6",
        "gain": "18",
        "queuedepth": "16",
        "chan_agc": "no",
        "hang": "0",
        "recovery": "100",
    },
    "rand_on": {
        "dither": "yes",
        "rand": "yes",
        "att": None,
        "gain": None,
        "queuedepth": "16",
        "chan_agc": "no",
        "hang": "0",
        "recovery": "100",
    },
    "dither_off": {
        "dither": "no",
        "rand": None,
        "att": None,
        "gain": None,
        "queuedepth": "16",
        "chan_agc": "no",
        "hang": "0",
        "recovery": "100",
    },
    "queue32": {
        "dither": "yes",
        "rand": None,
        "att": None,
        "gain": None,
        "queuedepth": "32",
        "chan_agc": "no",
        "hang": "0",
        "recovery": "100",
    },
}


def upsert_rx888(text: str, v: dict) -> str:
    # Operate only inside [rx888] ... next section
    m = re.search(r"(?s)(\[rx888\].*?)(\n\[|\Z)", text)
    if not m:
        raise SystemExit("no [rx888] section")
    body, tail = m.group(1), m.group(2)

    def setk(body: str, key: str, val: str | None) -> str:
        body = re.sub(rf"(?m)^[ \t]*#?[ \t]*{key}[ \t]*=.*\n", "", body)
        if val is None:
            return body
        return body.rstrip() + f"\n{key} = {val}\n"

    body = setk(body, "dither", v["dither"])
    body = setk(body, "rand", v["rand"])
    body = setk(body, "att", v["att"])
    body = setk(body, "gain", v["gain"])
    body = setk(body, "queuedepth", v["queuedepth"])
    return text[: m.start(1)] + body + text[m.end(1) :]


def upsert_channels(text: str, v: dict) -> str:
    def one(sec: str) -> str:
        for key, val in (
            ("agc", v["chan_agc"]),
            ("hang-time", v["hang"]),
            ("recovery-rate", v["recovery"]),
        ):
            if re.search(rf"(?m)^[ \t]*{key}[ \t]*=", sec):
                sec = re.sub(rf"(?m)^[ \t]*{key}[ \t]*=.*$", f"{key} = {val}", sec)
            else:
                sec = sec.rstrip() + f"\n{key} = {val}\n"
        return sec

    parts = re.split(r"(?=^\[HFDL)", text, flags=re.M)
    out = [parts[0]]
    for p in parts[1:]:
        out.append(one(p))
    return "".join(out)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("variant", choices=sorted(VARIANTS))
    args = ap.parse_args()
    v = VARIANTS[args.variant]
    text = CONF.read_text()
    text = upsert_rx888(text, v)
    text = upsert_channels(text, v)
    CONF.write_text(text)
    print(f"applied variant={args.variant}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
