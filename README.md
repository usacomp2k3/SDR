# WP-KMCO multi-mode station

This tree is a **reference** for how the station is wired (compose, host units, ka9q configs). It is not an installer — cloning it will not stand up a receiver.

Site coordinates and feeder keys stay in a local `.env` (see `.env.example`). Published fallbacks use **KMCO** (28.4294, −81.3089), not the house pin. Same idea for `radiosonde/station.cfg` and `rx888/ham/ham.env`. Host username, hostname, and Tailscale IPs are not in this tree.

**Full system reference (hardware, software chains, feeds):**  
→ **[Architecture.md](./Architecture.md)**

## What’s in this tree

These are **our** wiring, units, and configs. They are not the upstream projects.

| Path | What we keep here |
|------|-------------------|
| [`vhf-r2/`](vhf-r2/) | Airspy R2 ka9q `radiod` + host acarsdec/dumpvdl2 |
| [`rx888/`](rx888/) | RX888 HFDL + FT8/FT4/WSPR (`radiod-hfdl.conf`, host scripts) |
| [`irdm.conf`](irdm.conf) / [`irdm/`](irdm/) | Iridium Mini pin + libacars build notes |
| [`adsb/`](adsb/) | ultrafeeder / dump978 compose helpers (no globe history) |
| [`portal/`](portal/) | station portal, status API, coverage map |
| [`radiosonde/`](radiosonde/) | auto_rx `station.cfg.example` |
| [`sdr-watch/`](sdr-watch/) · [`systemd/`](systemd/) · [`host/`](host/) | USBFS, firmware hook, watch units |
| [`docker-compose*.yml`](docker-compose.yml) | Hub, router, AIS, sonde, ADS-B feeders, parked USB decoders |

Nested source checkouts (`ka9q-radio`, `rx888_tools`, `libacars`, `ft8_lib`) stay **out of git**. Point at the real repos below instead of submodules — this is a station notebook, not a meta-superproject.

## Upstream

| Project | Used for | Upstream |
|---------|----------|----------|
| ka9q-radio | VHF + HF channelizer (`radiod` / `pcmrecord`) | [ka9q/ka9q-radio](https://github.com/ka9q/ka9q-radio) |
| acarsdec | VHF ACARS | [TLeconte/acarsdec](https://github.com/TLeconte/acarsdec) · [docker-acarsdec](https://github.com/sdr-enthusiasts/docker-acarsdec) |
| dumpvdl2 | VDL2 | [szpajder/dumpvdl2](https://github.com/szpajder/dumpvdl2) · [docker-dumpvdl2](https://github.com/sdr-enthusiasts/docker-dumpvdl2) |
| dumphfdl | HFDL | [szpajder/dumphfdl](https://github.com/szpajder/dumphfdl) · [docker-dumphfdl](https://github.com/sdr-enthusiasts/docker-dumphfdl) |
| libacars | FANS/ADS-C unpack | [szpajder/libacars](https://github.com/szpajder/libacars) |
| ACARS Hub / acars_router | UI + feed fan-out | [docker-acarshub](https://github.com/sdr-enthusiasts/docker-acarshub) · [acars_router](https://github.com/sdr-enthusiasts/acars_router) |
| gr-iridium / iridium-toolkit | Iridium | [muccc/gr-iridium](https://github.com/muccc/gr-iridium) · [docker-gr-iridium-toolkit](https://github.com/jkrasuk/docker-gr-iridium-toolkit) |
| RX888 tools / firmware | FX3 load, `fx3_cmd` | [ringof/rx888_tools](https://github.com/ringof/rx888_tools) · [ringof/rx888-firmware](https://github.com/ringof/rx888-firmware) |
| ft8_lib | FT8/FT4 decode | [ka9q/ft8_lib](https://github.com/ka9q/ft8_lib) |
| ftlib-pskreporter | PSK Reporter spots | [pjsg/ftlib-pskreporter](https://github.com/pjsg/ftlib-pskreporter) |
| ultrafeeder / tar1090 / graphs1090 | ADS-B 1090+978 | [docker-adsb-ultrafeeder](https://github.com/sdr-enthusiasts/docker-adsb-ultrafeeder) |
| airspy_adsb | 1090 demod | [sdr-enthusiasts/airspy_adsb](https://github.com/sdr-enthusiasts/airspy_adsb) |
| dump978 | UAT | [sdr-enthusiasts/docker-dump978](https://github.com/sdr-enthusiasts/docker-dump978) |
| shipfeeder | AIS | [sdr-enthusiasts/docker-shipfeeder](https://github.com/sdr-enthusiasts/docker-shipfeeder) |
| radiosonde_auto_rx | sondes | [projecthorus/radiosonde_auto_rx](https://github.com/projecthorus/radiosonde_auto_rx) |
| ADS-B feeders | FA / FR24 / RB / … | [sdr-enthusiasts](https://github.com/sdr-enthusiasts) images |

On this host, ADSb-Vue and MeshMonitor live in **sibling** directories (`~/adsbvue`, `~/meshmonitor`), not in this repo: [jrsphoto/adsbvue](https://github.com/jrsphoto/adsbvue), [Yeraze/meshmonitor](https://github.com/Yeraze/meshmonitor).

## Serial map (locked)

| Serial | Radio | Mode | Station id | Decoder | Gain |
|--------|--------|------|------------|---------|------|
| `35ac63dc2d86554f` | Airspy **R2** | **VHF mux** ACARS+VDL2 | `WP-KMCO-ACARS` / `-VDL2` | host ka9q `vhf-r2-*` | 15 linearity |
| `35ac63dc2d8e234f` | Airspy **Mini** | **Iridium** (6 MS/s) | `WP-KMCO-IRDM` | `irdm` | 21 (irdm.conf) |
| `0009042501933917` | RX888 **mk2** | **HFDL (all channels)** | `WP-KMCO-HFDL` | host ka9q+dumphfdl | AGC / dither |
| `2f52ff5de72635e8` | Airspy HF+ | HFDL spare (parked) | — | docker `dumphfdl` profile `hfplus-hfdl` | IFGR/RFGR |
| `129` | RTL Blog V4 | unused (was VDL2) | — | — | — |
| `162` | RTL R820T (V3-class) | AIS | `WP-KMCO-AIS` | `shipfeeder` | **auto** AGC |
| `402` | RTL Blog V4 | Radiosonde | `WP-KMCO-SONDE` | `radiosonde_auto_rx` | **-1** AGC |
| `10A862DC34914863` | Airspy **Mini** | ADS-B 1090 | WinterPark | `airspy_adsb` | auto |
| `978` | RTL R820T (V3-class) | UAT 978 | WinterPark | `dump978` | autogain |

Airspy family: **2× Mini + 1× R2 + 1× HF+**. Docker `acarsdec` / `dumpvdl2` are compose profile `legacy-usb` — do not start.

R2 on Lothar 118–137 **without splitter**. ka9q LO 138.0 MHz → RF ~128.6–137.4.

### Central Florida frequency sets

**POA ACARS** — ~129.125–131.850:
`129.125 129.350 129.525 130.025 130.425 130.450 130.825 131.125 131.425 131.450 131.475 131.525 131.550 131.650 131.725 131.825 131.850`

**VDL2** — NA extras + 25 kHz grid 136.700–136.975:
`136.100 136.300 136.650 136.700 136.725 136.750 136.775 136.800 136.825 136.850 136.875 136.900 136.925 136.950 136.975`

No dongle bias-T (external injectors).
VHF assignment is locked (see table above).
## Start / stop

```bash
sudo bash ~/acars-hub/refresh_all_the_things.sh           # apt + pull + up + settle + verify
sudo bash ~/acars-hub/refresh_all_the_things.sh --no-pull # apt + confirm local compose/scripts
sudo bash ~/acars-hub/refresh_all_the_things.sh --no-apt  # skip apt-get

cd ~/acars-hub
sg docker -c 'docker compose \
  -f docker-compose.yml \
  -f docker-compose.irdm.yml \
  -f docker-compose.acars.yml \
  -f docker-compose.vdl2.yml \
  -f docker-compose.hfdl.yml \
  -f docker-compose.extras.yml \
  ps'

sg docker -c 'docker compose ... down'   # stop all
```

## Dashboards

| Service | URL |
|---------|-----|
| ACARS Hub | http://127.0.0.1:8080 |
| Iridium beam map | http://127.0.0.1:8888 |
| AIS map | http://127.0.0.1:8090 |
| Radiosonde | http://127.0.0.1:5000 |
| Station portal | http://127.0.0.1:8880 |
| Aggregator status | http://127.0.0.1:8880/status.html |
| SDR / USB status | http://127.0.0.1:8880/sdr.html |
| tar1090 (ADS-B) | http://127.0.0.1:8085 |
| UAT 978 | http://127.0.0.1:9780 |
| graphs1090 | http://127.0.0.1:8085/graphs1090/ |

## Feeds

| Mode | Destination |
|------|-------------|
| ACARS | feed.airframes.io:5550 · acars.tryflightdeck.com:5550 · feed.adsbitalia.it:31109 |
| VDL2 | feed.airframes.io:5553 · acars.tryflightdeck.com:5555 |
| HFDL | feed.airframes.io:5556 |
| IRDM | feed.airframes.io:5590 |
| AIS | Airframes (no key), aiscatcher.org, BoatBeacon, ShipFinder, sdrmap |
| Sonde | sondehub.org · sdrmap (auto_rx logs) |
| ADS-B / UAT | FA, FR24, RB, OpenSky, PF, plane.watch, ADSBHub, sdrmap (1090+MLAT), community ultrafeeder feeds (incl. map.flights) |

## Config files

- `.env` — serials, gains, ports
- `irdm.conf` — Iridium Mini pin
- `vhf-r2/` — R2 ka9q radiod + host acarsdec/dumpvdl2
- `irdm/build-libacars.sh` — bookworm `libacars-2.so` for FANS unpack (`-m libacars`)
- `acars/libacars-enrich.py` — VHF ACARS FANS unpack (host, UDP :15550)
- `radiosonde/station.cfg` — auto_rx
- `docker-compose*.yml` — services (ADS-B: `docker-compose.adsb.yml`)
- `adsb/` — ultrafeeder graphs/globe history, dump978 state
