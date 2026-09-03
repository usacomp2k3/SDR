# WP-KMCO SDR Station — Architecture

Comprehensive reference for the multi-mode receive site.  
Goal: enough detail to **recreate the station from scratch**, understand every software chain, and know where each radio, feed, and UI lives.

| | |
|--|--|
| **Last verified** | 2026-08-10 |
| **Primary tree** | `~/acars-hub/` |
| **Timezone** | `America/New_York` |

---

## 1. Overview

This host is a **fixed multi-SDR aviation / marine / weather receive station** for Central Florida (Winter Park / north Orlando area). It decodes several modes in parallel, fans messages into a local hub UI, and feeds public aggregators.

| Mode | Band | Station ID | Aggregator |
|------|------|------------|------------|
| VHF ACARS | ~129–132 MHz | `WP-KMCO-ACARS` | Airframes `feed.airframes.io:5550` (UDP) |
| VDL2 | ~136.1–137.0 MHz | `WP-KMCO-VDL2` | Airframes `:5553` (TCP) |
| HFDL | HF (scanned) | `WP-KMCO-HFDL` | Airframes `:5556` (TCP) |
| Iridium ACARS | ~1616–1626.5 MHz | `WP-KMCO-IRDM` | Airframes `:5590` (TCP) + local hub |
| AIS | 162 MHz marine | `WP-KMCO-AIS` | Airframes AIS + community + keyed UDP feeds (see §7) |
| Radiosonde | 400.05–406 MHz | `WP-KMCO-SONDE` | SondeHub (`api.v2.sondehub.org`) |
| ADS-B / UAT | 1090 + 978 MHz | WinterPark (MLAT) | Local ultrafeeder (Airspy Mini + RTL 978); multi-aggregator |

**Removed / parked (not part of active station):**

- **xng** multi-mode station — systemd unit disabled (`xng-station.service`); home configs/logs removed 2026-08-10. Do not re-enable while Docker owns the Iridium Airspy.
- **Bare-metal acarsdec** (`~/acars/`) — removed; Docker `acarsdec` is canonical.
- **Netdata** — retired 2026-08-10 in favor of **graphs1090** (`:8085/graphs1090/`). Compose file deleted; do not reintroduce without need.

---

## 2. Host platform

Hardware moved **2026-08-10** (OS disk transplanted from a laptop → desktop). Published identity is the station, not the old laptop hostname.

| Item | Value |
|------|--------|
| Hostname | local only (not in git) |
| Chassis | Dell OptiPlex 5060 |
| OS | DragonOS Noble (Ubuntu 24.04-based), kernel `7.0.0-28-generic` (x86_64) |
| CPU | Intel Core i5-8500 @ 3.00 GHz (6c/6t) |
| RAM | 32 GiB |
| Storage | ~468 GB NVMe |
| Primary LAN | private (`eno1` wired) |
| Tailscale | private (not in git) |
| Docker | Engine + Compose plugin; station user in `docker` group (commands often via `sg docker -c '…'`) |

### PCIe USB expansion

| Item | Value |
|------|--------|
| Cards | Two StarTech hosts: **4-port** on CPU PEG `00:01.0` (Renesas `03:00`–`06:00`) + **2-port** on PCH `00:1d.0` (`0b:00`, `0c:00`). Both Pericom PI7C9X2G608GP @ 5 GT/s ×4 |
| Power | SATA power on **both** cards |
| Layout | 4-port PEG + 2-port PCH, Heltec on **front USB-C**. **Live (2026-08-23):** SDRs and RX888 on StarTech `xhci-pci-renesas` (8× uPD720202 enumerated). Onboard Intel xHCI: GPS + empty hubs only. |
| Linux | Driver is in-tree `xhci_pci_renesas` (not a StarTech vendor package). Needs firmware **`/lib/firmware/renesas_usb_fw.mem`** |
| Firmware | uPD720202 **2.0.2.6**, sha256 `177560c224c73d040836b17082348036430ecf59e8a32d7736ce2b80b2634f97`. Station copy: `~/acars-hub/host/renesas_usb_fw.mem`. Initramfs hook: `/etc/initramfs-tools/hooks/renesas-usb-fw` (source `~/acars-hub/host/renesas-usb-fw.hook`) |
| Without firmware | Kernel logs `failed to load firmware renesas_usb_fw.mem, fallback to ROM`; hosts then **HC died** on attach. That was the 2026-08-17 outage; with firmware + `intel_iommu=off` it is **cleared**. |

`linux-firmware` does **not** ship this blob. Recreate: `sudo install -m 644 ~/acars-hub/host/renesas_usb_fw.mem /lib/firmware/renesas_usb_fw.mem` and `sudo install -m 755 ~/acars-hub/host/renesas-usb-fw.hook /etc/initramfs-tools/hooks/renesas-usb-fw && sudo update-initramfs -u`. First boot with the file present may write RAM/ROM on the chips; allow extra time. Confirm dmesg has **no** `fallback to ROM` / `HC died` (2026-08-23: none this boot).

**USB power management off:** no autosuspend, no D3cold on USB hosts, ASPM performance/`pcie_aspm=off`. Persist: grub `usbcore.autosuspend=-1 pcie_aspm=off` (next to `usbfs_memory_mb=0`), `/etc/tmpfiles.d/usb-no-autosuspend.conf`, udev `99-usb-no-autosuspend.rules`, `renesas-usb-power.service`.
**VT-d / IOMMU:** grub `intel_iommu=off` is required for StarTech uPD720202 attach (without it: command-ring timeout, `HC died`). This host does not need VT-d.

### Host tuning required for multi-SDR

| Tuning | Why | How / status |
|--------|-----|----------------|
| **USBFS memory** | Default 16 MB is too small for multi-RTL + Airspy + RX888 | `usbcore` is **built-in**, so `modprobe.d` is ignored. Already persisted: grub `usbcore.usbfs_memory_mb=0`, `/etc/tmpfiles.d/usbfs-memory.conf`, `usbfs-memory.service` **before** `docker.service` |
| **Renesas USB firmware** | StarTech uPD720202 hosts die on ROM fallback | `/lib/firmware/renesas_usb_fw.mem` (2.0.2.6) + initramfs hook. Not in `linux-firmware`. |
| **USB power management** | Autosuspend / D3cold / ASPM caused StarTech `HC died` (2026-08-17) | grub `usbcore.autosuspend=-1 pcie_aspm=off` + udev + tmpfiles + `renesas-usb-power.service` |
| **CPU governor** | Leave **powersave** (i5-8500 still turbos to ~3.9 GHz under load) | — |
| **Park xng** | Fights Docker for Airspy R2 | `refresh_all_the_things.sh` disables `xng-station.service` if present |
| **SDRplay API** | `sdrplay.service` enabled | Running at `/opt/sdrplay_api/` — **no SDRplay hardware in current inventory**; leftover service, harmless |

### GPS

- **u-blox 7** USB GNSS (`1546:01a7`) → `/dev/ttyACM0` (on a powered hub chain under onboard USB).
- `gpsd` active; socket `127.0.0.1:2947`. `/etc/default/gpsd`: `DEVICES=""`, `USBAUTO=true` (auto-attach).
- auto_rx: still `gpsd_enabled = False` in `radiosonde/station.cfg` (fixed station pin used instead).
- Heltec Meshtastic has a fixed mesh position only; it does **not** feed host gpsd/auto_rx.

---

## 3. Site location & identity

Home pin is **not** in this tree. It lives in local `.env` (`ADSB_LAT` / `ADSB_LON` / `ADSB_ALT_M`), `radiosonde/station.cfg`, and MeshMonitor — see `.env.example` / `radiosonde/station.cfg.example`.

Public identity is Central Florida near KMCO/KORL (Winter Park).

| Field | Value | Used by |
|-------|--------|---------|
| Site | Winter Park, FL (near KMCO) | maps, MLAT name, docs |
| Antenna note (sonde) | `1/4 wave monopole` | SondeHub station description |
| Bias-T | **Off on all dongles** | External injectors only (`BIASTEE=false` / empty) |

**Station ID prefix:** `WP-KMCO`  
**Per-mode suffixes:** `-ACARS`, `-VDL2`, `-HFDL`, `-IRDM`, `-AIS`, `-SONDE`

Legacy xng station id (parked, configs removed): `AJ-MCO1`.

### Related remote receivers (Tailscale)

| Host | Tailscale IP | Role |
|------|--------------|------|
| `adsb-winterpark` | Tailscale (retired) | **Retired for ADS-B** after cutover (was adsb.im Pi; radios moved here) |
| `adsb-winterparkmobile` | Tailscale | Mobile ADS-B (optional) |

---

## 4. Hardware inventory (locked serial map)

Serials are **locked**. Do not reassign without updating `.env`, `irdm.conf`, and `radiosonde/station.cfg` together.

| USB serial | Hardware | VID:PID | Mode | Container | Gain (live config) | Bias-T |
|------------|----------|---------|------|-----------|--------------------|--------|
| `35ac63dc2d86554f` | Airspy **R2** (only R2) | `1d50:60a1` | VHF mux ACARS+VDL2 | host `vhf-r2-*` | **15** linearity | Off |
| `35ac63dc2d8e234f` | Airspy **Mini** | `1d50:60a1` | Iridium (6 MS/s) | `irdm` | **21** (irdm.conf) | Off |
| `2f52ff5de72635e8` | Airspy HF+ | `03eb:800c` | HFDL | `dumphfdl` | `IFGR=45,RFGR=2` | N/A |
| `129` | RTL-SDR Blog V4 | `0bda:2838` | unused (was VDL2) | — | — | Off |
| `162` | RTL V3-class (R820T) | `0bda:2838` | AIS | `shipfeeder` | **auto** + RTLAGC | Off |
| `402` | RTL-SDR Blog V4 | `0bda:2838` | Radiosonde | `radiosonde_auto_rx` | **-1** (AGC) | Off |
| `10A862DC34914863` | Airspy **Mini** | `1d50:60a1` | ADS-B 1090 | `airspy_adsb` | **auto** | Off |
| `978` | RTL V3-class (R820T) | `0bda:2838` | UAT 978 | `dump978` | **autogain** | Off |

Airspy family total: **2× Mini** (`*234f` Iridium, `10A862…` 1090) + **1× R2** (VHF mux) + **1× HF+**. USB product string is the same for Mini and R2 (`AIRSPY` / `1d50:60a1`); distinguish by serial.

**Non-SDR USB (same host):**

| Device | Serial / ID | Role |
|--------|-------------|------|
| u-blox 7 GPS | `1546:01a7` → `/dev/ttyACM0` | gpsd |
| Heltec V4 (ESP32-S3) | `A4:CB:8F:A7:84:E0` → `/dev/ttyACM*` when present | Meshtastic repeater (`CF-RPT-84e0` / `CFR1`) |
| Powered USB hubs | Genesys / Terminus | Split SDRs / GPS; 1090+978 share a front-hub chain |

**Typical USB placement (2026-08-23):** Live radios on **StarTech Renesas** (`xhci-pci-renesas`). RX888 SuperSpeed on a dedicated Renesas SS port; Airspy/RTL on other Renesas roots (some still behind a Terminus/Genesys HS hub). GPS on onboard Intel. Heltec on **front USB-C**. Iridium remains the USB-bandwidth canary if a high-rate stick shares a hub.

### VHF A/B test result (locked assignment)

Same cavity (Lothar 118–136 class path). VHF assignment locked in README.

| Config | ACARS radio | VDL2 radio | Result (sample) |
|--------|-------------|------------|-----------------|
| **A (winner)** | Airspy `*234f` | RTL `129` | ~62 total msgs |
| B (loser) | RTL | Airspy `*234f` | ~21; Airspy VDL2 = 0 |

Do **not** put VDL2 on Airspy `*234f` without re-testing.

---

## 5. RF / frequency plan

### VHF ACARS (POA — Central FL / CONUS)

`ACARS_FREQUENCIES` in `.env` (MHz):

```text
129.125 129.350 129.525 130.025 130.425 130.450 130.825
131.125 131.425 131.450 131.475 131.525 131.550 131.650
131.725 131.825 131.850
```

Omit 136.x (VDL2 / dual-use — handled by dumpvdl2).

### VDL2 (North America)

`VDL2_FREQUENCIES` (Hz):

```text
136100000 136300000 136650000
136700000 136725000 136750000 136775000 136800000 136825000
136850000 136875000 136900000 136925000 136950000 136975000
```

Sample rate: `105000 × VDL2_OVERSAMPLE` → with oversample **20** → **2.1 MS/s**.

### HFDL

- Airspy HF+ via Soapy: `driver=airspyhf,serial=2f52ff5de72635e8`
- Sample rate: `768000`
- Band scanner enabled (`ENABLE_SYSTABLE`, `ENABLE_BASESTATION`); no fixed `FREQUENCIES` list required
- Scanner state dir: `~/acars-hub/hfdl-scanner/`
- Data dir: `~/acars-hub/hfdl-data/`

### Iridium

| Param | Value |
|-------|--------|
| Center | 1622.0 MHz |
| Sample rate / bandwidth | 6 MS/s / 6 MHz (Mini; ~1619–1625) |
| Device string | `airspy=0x35AC63DC2D8E234F,pack=1` |
| Config file | `~/acars-hub/irdm.conf` |

### AIS

- Standard AIS channels via shipfeeder / AIS-Catcher image
- AFC wide enabled

### Radiosonde

| Param | Value |
|-------|--------|
| Scan | 400.05–406.0 MHz |
| Types seen locally | RS41, DFM17, iMet (logs under `radiosonde/log/`) |
| PPM | 0 |
| Upload | SondeHub v2 every 15 s |
| APRS | Disabled |

---

## 6. Software architecture

### 6.1 High-level data flow

```text
                    ┌─────────────────────────────────────────────────────────┐
                    │         OptiPlex host (Docker)                          │
                    │                                                         │
  Airspy R2 *6554f ► ka9q vhf-r2 ─┬─ acarsdec ─UDP:5550─►┐                    │
                     (LO 138 MHz) ├─ dumpvdl2 ─UDP:5555─►┤                    │
  Airspy Mini *234f ► irdm ──────UDP:5558───────────────►├──► acars_router     │
  RX888 ──────────► dumphfdl ────UDP:5556───────────────►┘         │          │
                                                    │         │               │
                                                    │ ZMQ     │               │
                                                    ▼         │               │
                                              acarshub :8080  │               │
                                              (UI + history)  │               │
                                                              │               │
  RTL 162 ────────► shipfeeder :8090 ─────────────────────────┼──► AIS aggs   │
  RTL 402 ────────► radiosonde_auto_rx :5000 ─────────────────┼──► SondeHub   │
                                                              │               │
  Airspy Mini ────► airspy_adsb ─┐                            │               │
  RTL 978 ────────► dump978 ─────┼─► ultrafeeder :8085 ───────┼──► ADS-B aggs  │
                                 │    tar1090 + graphs1090    │   + MLAT       │
                                 └──► acarshub overlay + adsbvue :24556        │
  Heltec USB ─────► socat :4403 ──► meshmonitor :3001         │  (no RF feed) │
  u-blox GPS ─────► gpsd :2947 (host; auto_rx not subscribed) │               │
                    └─────────────────────────────────────────────────────────┘
```

### 6.2 Compose overlay model

Everything lives under `~/acars-hub/`. Overlays are **merged**:

| File | Services |
|------|----------|
| `docker-compose.yml` | `acarshub`, `acars_router` |
| `docker-compose.acars.yml` | `acarsdec` (profile `legacy-usb`, parked) |
| `docker-compose.vdl2.yml` | `dumpvdl2` (profile `legacy-usb`, parked) |
| `docker-compose.hfdl.yml` | `dumphfdl` (+ router ZMQ sub) |
| `docker-compose.irdm.yml` | `irdm` |
| `docker-compose.extras.yml` | `shipfeeder`, `radiosonde_auto_rx` |
| `docker-compose.adsb.yml` | `airspy_adsb`, `dump978`, `ultrafeeder`, piaware/fr24/rb/opensky/pf/pw/adsbhub, `sdrmap` |
| `docker-compose.portal.yml` | `station-status-api`, `station-portal` (nginx) |

**Note:** `refresh_all_the_things.sh` includes the portal overlay. Do **not** run `compose up --remove-orphans` on a partial radio-only file set or the portal will be deleted.

**Full radio stack start (canonical):**

```bash
bash ~/acars-hub/refresh_all_the_things.sh
# equivalent:
cd ~/acars-hub
sg docker -c 'docker compose \
  -f docker-compose.yml \
  -f docker-compose.irdm.yml \
  -f docker-compose.acars.yml \
  -f docker-compose.vdl2.yml \
  -f docker-compose.hfdl.yml \
  -f docker-compose.extras.yml \
  -f docker-compose.adsb.yml \
  pull && docker compose \
  -f docker-compose.yml \
  -f docker-compose.irdm.yml \
  -f docker-compose.acars.yml \
  -f docker-compose.vdl2.yml \
  -f docker-compose.hfdl.yml \
  -f docker-compose.extras.yml \
  -f docker-compose.adsb.yml \
  up -d'
```

`refresh_all_the_things.sh` **pulls images then `up -d`** (then settles and verifies). Reboot alone does **not** pull; it only restarts existing containers with the images already on disk.

Optional portal (separate):

```bash
sg docker -c 'docker compose -f docker-compose.portal.yml up -d'
```

### 6.3 Images (current)

| Container | Image |
|-----------|--------|
| `acarshub` | `ghcr.io/sdr-enthusiasts/docker-acarshub:latest` |
| `acars_router` | `ghcr.io/sdr-enthusiasts/acars_router:latest` |
| `acarsdec` | `ghcr.io/sdr-enthusiasts/docker-acarsdec:latest` |
| `dumpvdl2` | `ghcr.io/sdr-enthusiasts/docker-dumpvdl2:latest` |
| `dumphfdl` | `ghcr.io/sdr-enthusiasts/docker-dumphfdl:latest` |
| `irdm` | `ghcr.io/jkrasuk/docker-gr-iridium-toolkit:latest` |
| `shipfeeder` | `ghcr.io/sdr-enthusiasts/docker-shipfeeder:latest` |
| `radiosonde_auto_rx` | `ghcr.io/projecthorus/radiosonde_auto_rx:latest` |
| `station-status-api` | `python:3.12-alpine` (+ `./portal/status-api/app.py`) |
| `station-portal` | `nginx:alpine` |
| `airspy_adsb` | `ghcr.io/sdr-enthusiasts/airspy_adsb:latest` |
| `dump978` | `ghcr.io/sdr-enthusiasts/docker-dump978:latest` |
| `ultrafeeder` | `ghcr.io/sdr-enthusiasts/docker-adsb-ultrafeeder:latest` |
| `piaware` / `fr24feed` / `rbfeeder` / … | sdr-enthusiasts commercial feeder images |
| `adsbvue` | `ghcr.io/jrsphoto/adsbvue:latest` (separate project dir) |
| `meshmonitor` | `ghcr.io/yeraze/meshmonitor:latest` (separate project dir) |

### 6.4 Per-mode software chains

#### ACARS (VHF)

```text
Airspy Mini (*234f)
  → docker-acarsdec (FEED_ID=WP-KMCO-ACARS)
  → UDP to acars_router:5550
  → router fan-out:
       • UDP feed.airframes.io:5550
       • ZMQ serve 45550 → acarshub ACARS_CONNECTIONS
```

Key env: `ACARS_AIRSPY_SERIAL`, `ACARS_GAIN=15`, `ACARS_FREQUENCIES`, `BIASTEE=false`, `PPM=0`.

FANS unpack: `acars/acars_bridge` + `acars/libacars-enrich.py` replace the rust hop so VHF ADS-C/CPDLC get a `libacars` field (image already has `libacars-2.so`).

#### VDL2

```text
RTL Blog V4 (129)
  → docker-dumpvdl2 (ZMQ server 0.0.0.0:45555, FEED_ID=WP-KMCO-VDL2)
  → acars_router AR_RECV_ZMQ_VDLM2=dumpvdl2:45555
  → TCP feed.airframes.io:5553
  → ZMQ 45555 → acarshub VDLM
```

Key env: `VDL2_RTL_SERIAL=129`, `VDL2_GAIN=32.0`, `VDL2_OVERSAMPLE=20`, `VDL2_FREQUENCIES`.

#### HFDL

```text
Airspy HF+ (Soapy)
  → docker-dumphfdl (ZMQ server :45556, FEED_ID=WP-KMCO-HFDL)
  → acars_router AR_RECV_ZMQ_HFDL=dumphfdl:45556
  → TCP feed.airframes.io:5556
  → ZMQ → acarshub HFDL
```

Key env: `HFDL_SOAPYSDR`, `HFDL_GAIN`, `DUMP_HFDL_COMMAND_EXTRA=--fft-threads 12`.

#### Iridium

```text
Airspy R2 (*86554f, only R2) via irdm.conf
  → gr-iridium extractor (EXTRACTOR_ARGS)
  → iridium-toolkit parser (PARSER_ARGS)
  → iridium-acars pipeline (`-m libacars` via `irdm/iridium-reassembler`)
  → ACARS_ADDITIONAL_OUTPUTS=udp:acars_router:5558  (hub)
  → also direct TCP feed.airframes.io:5590 (image default)
  → beam map :8888, MT map :8889
```

| Arg source | Default in compose / `.env` |
|------------|------------------------------|
| `IRDM_EXTRACTOR_ARGS` | `-D 8 --multi-frame -d 15 -q 1000 -b 2000` (`.env`; compose default was higher) |
| `IRDM_PARSER_ARGS` | `--harder --uw-ec --stats` |
| Gain | `21` in `irdm.conf` |
| FANS unpack | `irdm/lib/libacars-2.so` (bookworm build) + override script `irdm/iridium-reassembler` |

Build the `.so` (host DragonOS copy is ICU 74, will not load in irdm): `bash ~/acars-hub/irdm/build-libacars.sh`. Recreate **only** `irdm` after that.

Logs: `~/acars-hub/irdm-logs/`.

#### AIS

```text
RTL 162
  → docker-shipfeeder (AIS-Catcher based)
  → Web UI :8090
  → Airframes AIS (AIRFRAMES_STATION_ID=WP-KMCO-AIS)
  → aiscatcher.org, BoatBeacon, ShipFinder (no-key feeds)
```

Data: `~/acars-hub/ais-data/`.

No-key: Airframes, aiscatcher.org, BoatBeacon, ShipFinder.  
Keyed UDP (configured in `.env`): MarineTraffic, MyShipTracking, ShipXplorer, VesselFinder, AISHub.

#### Radiosonde

```text
RTL 402
  → radiosonde_auto_rx (host network)
  → scan 400.05–406 MHz, decode RS41/DFM/iMet/…
  → SondeHub upload (callsign WP-KMCO-SONDE)
  → UI http://127.0.0.1:5000
  → per-sonde logs in radiosonde/log/
```

Config mount: `./radiosonde/station.cfg` → `/opt/auto_rx/station.cfg` (**read-only**; recreate container after edits).

#### ADS-B / UAT (local — migrated from adsb.im Pi)

```text
Airspy Mini (10A862DC34914863)
  → airspy_adsb (beast :30005)
RTL 978
  → dump978 (uat :30978, UI :9780)
  → ultrafeeder (READSB_NET_ONLY)
       • tar1090 / graphs1090 :8085
       • community ULTRAFEEDER_CONFIG feeds + MLAT
       • commercial sidecars: piaware, fr24, rbfeeder, opensky, planefinder, planewatch, adsbhub, sdrmap
  → acarshub ADSB_URL=http://ultrafeeder/data/aircraft.json
  → adsbvue http://host.docker.internal:8085
  acars2pos (router TCP 15550/555/556/558) → ultrafeeder JAERO :32009 (tar1090 ADS-C)
  acars_router TCP 15550/555/556/558 → acars2pos → ultrafeeder JAERO SBS :32009 (tar1090 ADS-C icons)
  tar1090 ADS-C hold: READSB_JAERO_TIMEOUT=15 min (image default 720 = 12h ghosts)
```

| Item | Value |
|------|--------|
| Site / MLAT name | WinterPark |
| Lat / Lon / Alt | local `.env` (`ADSB_LAT` / `ADSB_LON` / `ADSB_ALT_M`) |
| Compose | `docker-compose.adsb.yml` |
| Data | `~/acars-hub/adsb/ultrafeeder/{graphs1090,globe_history}`, `adsb/dump978/` |
| History | Live RRDs / globe under `adsb/`; Pi migration zips **removed** 2026-08-10 after cutover |
| Cutover helper | `adsb/scripts/cutover-checklist.sh` |
| System metrics | **graphs1090** only (Netdata removed) |

**Do not** run the Pi feeder stack with the same sharing keys after cutover.

---

## 7. Aggregators & outbound feeds

### Airframes (aviation datalink)

| Mode | Protocol | Destination | Station ID |
|------|----------|-------------|------------|
| ACARS | UDP | `feed.airframes.io:5550` | `WP-KMCO-ACARS` |
| ACARS | UDP | `feedthe.acarsdrama.com:5550` | (same station id) |
| ACARS | UDP | `data.avdelphi.com:5556` | claim on avdelphi.com |
| ACARS | TCP | `feed-acars.adsb.lol:5550` | adsb.lol datalink |
| ACARS | UDP | `acars.tryflightdeck.com:5550` | FlightDeck (VHF ACARS) |
| ACARS | TCP | `feed.adsbitalia.it:31109` | ADSBItalia (VHF ACARS) |
| VDL2 | TCP | `feed.airframes.io:5553` | `WP-KMCO-VDL2` |
| VDL2 | UDP | `feedthe.acarsdrama.com:5555` · `data.avdelphi.com:5600` | |
| VDL2 | TCP | `feed-acars.adsb.lol:5552` | |
| VDL2 | UDP | `acars.tryflightdeck.com:5555` | FlightDeck |
| HFDL | TCP | `feed.airframes.io:5556` · `feed-acars.adsb.lol:5551` | `WP-KMCO-HFDL` |
| IRDM | TCP | `feed.airframes.io:5590` | `WP-KMCO-IRDM` |
| AIS | (shipfeeder) | Airframes AIS ingest | `WP-KMCO-AIS` |

Router: `AR_ENABLE_DEDUPE=true`. Station IDs are preserved per decoder (`FEED_ID` / `STATION_ID`).

### SondeHub

- Uploader callsign: `WP-KMCO-SONDE`
- `sondehub_enabled = True`, upload rate 15 s
- Listener position upload: **on**
- Tracker: https://tracker.sondehub.org/

### AIS community + keyed

- aiscatcher.org (`AISCATCHER_SHAREDATA=true`), BoatBeacon, ShipFinder (no key)
- MarineTraffic / MyShipTracking / ShipXplorer / VesselFinder / AISHub via UDP ports in `.env`

### ADS-B aggregators (local ultrafeeder)

| Class | Destinations |
|-------|----------------|
| Community (beast_reduce_plus + mlat) | adsb.fi, adsb.lol, airplanes.live, ADSBx, planespotters, theairtraffic, dataero, adsbitalia, flyitaly, avdelphi, flyoverhead, flyrealtraffic, flightdeck, hpradar, adsbiq, map.flights (ADS-B only, no MLAT), … |
| Commercial sidecars | FlightAware, Flightradar24 (+UAT), AirNav Radar, OpenSky, PlaneFinder, plane.watch, ADSBHub |

UUIDs / sharing keys: WinterPark identity in `.env` (from adsb.im backup).

### Local only (not fed off-host as primary)

- ACARS Hub message DB / UI
- Iridium beam/MT maps
- ADSb-Vue coverage (local ultrafeeder)
- MeshMonitor

---

## 8. Internal routing (acars_router)

Relevant live settings (container `acars_router`):

| Direction | Setting |
|-----------|---------|
| Inbound ACARS UDP | listen `5550` (from acarsdec) |
| Inbound VDL2 ZMQ | `AR_RECV_ZMQ_VDLM2=dumpvdl2:45555` |
| Inbound HFDL ZMQ | `AR_RECV_ZMQ_HFDL=dumphfdl:45556` |
| Inbound IRDM UDP | listen `5558` (from irdm) |
| Outbound ACARS | `AR_SEND_UDP_ACARS=feed.airframes.io:5550` |
| Outbound VDL2 | `AR_SEND_TCP_VDLM2=feed.airframes.io:5553` |
| Outbound HFDL | `AR_SEND_TCP_HFDL=feed.airframes.io:5556` |
| Outbound IRDM | `AR_SEND_TCP_IRDM=feed.airframes.io:5590` |
| Hub ZMQ serves | ACARS `45550`, VDL2 `45555`, HFDL `45556`, IRDM `45558` |

ACARS Hub connections:

```text
ACARS_CONNECTIONS=zmq://acars_router:45550
VDLM_CONNECTIONS=zmq://acars_router:45555
IRDM_CONNECTIONS=zmq://acars_router:45558
HFDL_CONNECTIONS=zmq://acars_router:45556
```

History: `DB_SAVE_DAYS=30`, `DB_ALERT_SAVE_DAYS=180`, `DB_SAVEALL=true` (volume `./acars_data`).

---

## 9. Local dashboards & ports

| Service | URL | Port |
|---------|-----|------|
| **Station portal** (index of all UIs) | http://127.0.0.1:8880 | 8880 |
| Aggregator status | http://127.0.0.1:8880/status.html | 8880 (+ API 8881) |
| ACARS Hub | http://127.0.0.1:8080 | 8080 |
| tar1090 / ultrafeeder | http://127.0.0.1:8085 | 8085 |
| graphs1090 | http://127.0.0.1:8085/graphs1090/ | 8085 |
| UAT dump978 | http://127.0.0.1:9780 | 9780 |
| PiAware map (optional) | http://127.0.0.1:8081 | 8081 |
| Iridium beam map | http://127.0.0.1:8888 | 8888 |
| Iridium MT map | http://127.0.0.1:8889 | 8889 |
| AIS (shipfeeder) | http://127.0.0.1:8090 | 8090 |
| Radiosonde auto_rx | http://127.0.0.1:5000 | 5000 (host network) |
| ADSb-Vue | http://127.0.0.1:24556 | 24556 |
| MeshMonitor | http://127.0.0.1:3001 | 3001 (host network) |

Portal source: `~/acars-hub/portal/index.html` (resolves hostname for LAN/Tailscale).  
**Netdata removed** — use graphs1090 for host/decoder graphs.

---

## 10. Configuration file map

| Path | Purpose |
|------|---------|
| `~/acars-hub/.env` | **Master knobs**: serials, gains, freqs, ports, station id, ADS-B keys/UUIDs |
| `~/acars-hub/irdm.conf` | gr-iridium Airspy pin, sample rate, gain |
| `~/acars-hub/irdm/` | Bookworm `libacars-2.so` + `iridium-reassembler` (`-m libacars`) |
| `~/acars-hub/radiosonde/station.cfg` | auto_rx full config (location, scan, SondeHub, filters) |
| `~/acars-hub/docker-compose*.yml` | Service definitions / overlays |
| `~/acars-hub/adsb/` | ultrafeeder graphs + globe history, dump978 state |
| `~/acars-hub/portal/index.html` | Portal card list (no Netdata card) |
| `~/acars-hub/portal/status-api/app.py` | Aggregator status API for portal |
| `~/adsbvue/docker-compose.yml` | ADSb-Vue → local ultrafeeder `:8085` |
| `~/meshmonitor/docker-compose.yml` | MeshMonitor UI |
| `/etc/tmpfiles.d/usbfs-memory.conf` + `usbfs-memory.service` + grub `usbcore.usbfs_memory_mb=0` | USBFS unlimited (`0`). `modprobe.d` is a no-op (usbcore builtin) |
| `/etc/default/gpsd` | `USBAUTO=true`; USB GPS attaches as `/dev/ttyACM0` |

### Critical `.env` keys (recreation checklist)

```bash
STATION_ID=WP-KMCO
TZ=America/New_York
ADSB_LAT=          # local only — not in git
ADSB_LON=
ADSB_ALT_M=
ADSB_URL=http://ultrafeeder/data/aircraft.json
AIRSPY_ADSB_SERIAL=0x10A862DC34914863
UAT_RTL_SERIAL=978
# + FEEDER_* keys / ULTRAFEEDER_CONFIG (see .env)

ACARS_AIRSPY_SERIAL=35ac63dc2d8e234f
ACARS_GAIN=15
ACARS_FREQUENCIES=…   # see §5

VDL2_RTL_SERIAL=129
VDL2_GAIN=32.0
VDL2_OVERSAMPLE=20
VDL2_FREQUENCIES=…    # see §5

HFDL_SOAPYSDR=driver=airspyhf,serial=2f52ff5de72635e8
HFDL_GAIN_TYPE=--gain-elements
HFDL_GAIN=IFGR=45,RFGR=2
HFDL_SAMPLERATE=768000

AIS_RTL_SERIAL=162
AIS_GAIN=auto
AIS_BANDWIDTH=192K   # AIS-catcher -a tuner BW; sample rate is device default ~1536 kS/s
AIRFRAMES_AIS_ID=WP-KMCO-AIS

RADIOSONDE_RTL_SERIAL=402   # also device_idx in station.cfg
```

`irdm.conf` device (must match serial map):

```text
device_args='airspy=0x35AC63DC2D86554F,pack=1'
sample_rate=10000000
center_freq=1622000000
gain=21
```

---

## 11. Related projects on this host

### 11.1 ADSb-Vue (`~/adsbvue/`)

- Pulls from **local** ultrafeeder `http://host.docker.internal:8085` (tar1090 + chunks).
- Receiver pin: same as `.env` `ADSB_LAT` / `ADSB_LON` (WinterPark; not in git).
- UI: `:24556`; data volume `./data` (90-day retain).

```bash
cd ~/adsbvue && sg docker -c 'docker compose up -d'
```

### 11.2 MeshMonitor + Heltec (`~/meshmonitor/`, `~/meshtastic-flash/`)

| Item | Value |
|------|--------|
| Board | Heltec V4, Meshtastic firmware `heltec-v4 2.7.26.54e0d8d` |
| Role | ROUTER_LATE, US region, LONG_FAST |
| Name | `CF-RPT-84e0` / short `CFR1` |
| Bridge | Host systemd user unit `meshtastic-socat-bridge.service` → USB serial → TCP **4403** |
| UI | meshmonitor Docker, host network, port **3001** |
| Login | `admin` / `changeme` (change after first login) |

Do **not** bind `/dev/ttyACM*` into the meshmonitor container while the host socat bridge owns the port.

### 11.3 Parked xng (removed from home)

- Airframes xng was an alternate single-process stack (Iridium / `AJ-MCO1`).
- Unit remains **disabled** if present under `/etc/systemd/system/xng-station.service`.
- Home files (`station.toml`, install script, logs, package) **removed** 2026-08-10.
- Do not re-enable while Docker `irdm` owns Airspy `*86554f`.

---

## 12. Operational runbook

### Start everything

```bash
bash ~/acars-hub/refresh_all_the_things.sh          # pull + up radio/aggregator compose set
# portal (included in refresh_all_the_things.sh):
cd ~/acars-hub
sg docker -c 'docker compose -f docker-compose.portal.yml up -d'
# mesh
systemctl --user enable --now meshtastic-socat-bridge.service
cd ~/meshmonitor && sg docker -c 'docker compose up -d'
# ADS-B view
cd ~/adsbvue && sg docker -c 'docker compose up -d'
```

### Stop aviation stack

```bash
cd ~/acars-hub
sg docker -c 'docker compose \
  -f docker-compose.yml \
  -f docker-compose.irdm.yml \
  -f docker-compose.acars.yml \
  -f docker-compose.vdl2.yml \
  -f docker-compose.hfdl.yml \
  -f docker-compose.extras.yml \
  down'
```

### Status / logs

```bash
sg docker -c 'docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"'
sg docker -c 'docker logs -f acarshub'
sg docker -c 'docker logs -f irdm'
sg docker -c 'docker logs -f radiosonde_auto_rx'
```

### After editing `station.cfg` or `.env`

```bash
# station.cfg is mounted RO — recreate auto_rx
cd ~/acars-hub
sg docker -c 'docker compose -f docker-compose.yml -f docker-compose.extras.yml up -d --force-recreate radiosonde_auto_rx'

# .env changes need recreate of affected services
sg docker -c 'docker compose -f docker-compose.yml -f docker-compose.acars.yml up -d --force-recreate acarsdec'
# …same pattern for dumpvdl2, dumphfdl, shipfeeder, acarshub
```

### Backup

```bash
bash ~/bin/backup-station.sh              # config zip → ~/backups/station/
bash ~/bin/backup-station.sh --with-data  # include DBs/logs
```

### VHF A/B retest

```bash
# VHF A/B script removed; assignment locked in README.
```

### USB / multi-SDR recovery

Already installed on this host (do not re-apply from a deleted script):
grub `usbcore.usbfs_memory_mb=0`, `/etc/tmpfiles.d/usbfs-memory.conf`,
`usbfs-memory.service` before Docker, RX888 systemd units.

**Decoder reopen (hands-off):** **system** `sdr-watch.timer` every 1 min + udev
on radio VID:PID add. Script `~/acars-hub/sdr-watch/sdr-watch.py` reads the
Equipmentlist USB table. Flock = one scan at a time (hub replug / many udev
events). Cooldown stamps only after a successful restart. Unplugged radios are
left alone. RX888 DFU (`04b4:00f3`) is skipped until app mode (`00f1`);
`health-fix.sh` then starts **systemd** `rx888-radiod` + `rx888-pcmrecord`.
**Do not** enable the user timer — it raced the system timer and killed radiod.
`refresh_all_the_things.sh` installs units, enables RX888 services, and masks
the user timer.

### Image updates

```bash
bash ~/acars-hub/refresh_all_the_things.sh    # compose pull + up -d for radio stack
# reboot does NOT pull new images
```

---

## 13. Recreate from scratch (checklist)

1. **Host**
   - Install Ubuntu/DragonOS (or similar), a station user, Docker Engine + Compose.
   - `usermod -aG docker <station-user>`; log out/in.
   - USBFS/RX888 persist is already on this host (grub + systemd). Recreate those units from `~/acars-hub/systemd/` and `~/acars-hub/rx888/systemd/` if building a new box. Also install `~/acars-hub/sdr-watch/` (timer + udev) so decoders reopen after a USB reset.
   - Optional: CPU performance governor.
   - Install Tailscale if using remote UI access.
   - Optional: PEXUSB3S44V with SATA power + rear bracket; migrate Airspys off shared mobo hub when stable.

2. **Hardware**
   - Flash unique RTL serials with `rtl_eeprom` (**never** leave `00000000` / `00000001` if multi-RTL):
     - `129` → VDL2, `162` → AIS, `402` → sonde; `978` → UAT (dump978).
   - Note Airspy serials from `lsusb` / device labels; match table in §4 (2× Mini + 1× R2 + HF+).
   - External bias injectors only — leave dongle bias-T off.
   - Connect antennas for VHF cavity, AIS, UHF sonde, L-band Iridium, HF as deployed.
   - Optional: u-blox USB GPS → gpsd.

3. **Clone / restore config**
   - Restore `~/acars-hub/` from `~/bin/backup-station.sh` zip or equivalent + secrets.
   - Ensure `.env`, `irdm.conf`, `radiosonde/station.cfg` serials and lat/lon match §3–§4.
   - `mkdir -p acars_data irdm-logs hfdl-data hfdl-scanner ais-data radiosonde/log adsb/ultrafeeder/{graphs1090,globe_history} adsb/dump978`

4. **Bring up stack**
   - `bash ~/acars-hub/refresh_all_the_things.sh`
   - Portal / adsbvue / meshmonitor as needed.

5. **Verify**
   - Each container healthy; each SDR claimed by the correct process.
   - ACARS Hub shows ACARS/VDL2/HFDL/IRDM traffic.
   - Airframes station list shows `WP-KMCO-*`.
   - SondeHub shows `WP-KMCO-SONDE` at correct lat/lon.
   - AIS map on `:8090`; tar1090/graphs1090 on `:8085`.
   - Portal `:8880` links all UIs.

6. **Do not**
   - Run xng and `irdm` on the same Airspy simultaneously.
   - Assign VDL2 to Airspy `*234f` without re-running A/B test.
   - Enable dongle bias-T with external injectors already powered.
   - `docker compose … --remove-orphans` on the radio-only file set (kills portal).

---

## 14. Scripts reference

| Script | Role |
|--------|------|
| `refresh_all_the_things.sh` | `compose pull` + `up -d` + ordered settle + verify (radio/portal/adsbvue/mesh + host RX888 HFDL); parks xng if present |


| `~/bin/backup-station.sh` | Config (and optional data) backup zip |

---

## 15. Known decisions & open items

| Decision | Rationale |
|----------|-----------|
| ACARS on Airspy Mini `*234f`, VDL2 on RTL 129 | A/B test winner |
| Iridium on dedicated Airspy R2 `*86554f` | Only R2; needs 10 MS/s; pinned in `irdm.conf` |
| Two Minis (ACARS + 1090), not two R2s | Operator correction 2026-08-10; docs previously mislabeled `*234f` as R2 |
| Host network for auto_rx | UI on host `:5000` without port map |
| No dongle bias-T | External injectors only |
| Coarse KMCO coords retired | Station pin = adsbvue/mesh home site (local `.env`, not in git) |
| xng parked / home files removed | Docker stack is primary |
| Netdata removed | graphs1090 covers station decode/system graphs; lighter footprint |
| Host = OptiPlex 5060 (disk from laptop) | 2026-08-10 transplant; hostname is local-only |

| Open item | Notes |
|-----------|--------|
| StarTech port map vs physical jacks | Hosts **up** (2026-08-23: no `HC died` this boot; radios on Renesas). Confirm 4+2 jack map vs PCI `03:00`–`06:00` / `0a:00`–`0d:00` |
| Confirm 4+2 device map vs physical ports | Dedicated channel per high-rate Airspy / RX888 |
| auto_rx ← gpsd | GPS present on `/dev/ttyACM0`; still `gpsd_enabled=False` (fixed pin) |
| SDRplay service | Enabled but unused — remove if no RSPdx ever planned |
| README gain table drift | README historically listed different gains; **trust `.env` + `irdm.conf` + live container env** |
| Optional: fold portal into `refresh_all_the_things.sh` | Avoids orphan warning; currently separate compose file |

---

## 16. Directory tree (operational)

```text
~/acars-hub/
  Architecture.md          ← this document
  README.md                ← short operator cheat-sheet
  .env                     ← master serials / gains / freqs
  docker-compose.yml       ← hub + router
  docker-compose.acars.yml
  docker-compose.vdl2.yml
  docker-compose.hfdl.yml
  docker-compose.irdm.yml
  docker-compose.extras.yml
  docker-compose.adsb.yml
  docker-compose.portal.yml
  irdm.conf
  irdm/                   ← libacars bookworm .so + reassembler override
  radiosonde/station.cfg
  radiosonde/log/          ← per-sonde CSV logs
  acars_data/              ← hub persistence
  adsb/                    ← graphs1090 + globe_history + dump978
  ais-data/
  hfdl-data/
  hfdl-scanner/
  irdm-logs/
  portal/                  ← index.html + status-api + nginx.conf
  refresh_all_the_things.sh, …

~/adsbvue/                 ← ADS-B viewer (local ultrafeeder :8085)
~/meshmonitor/             ← Meshtastic UI + bridge scripts
~/meshtastic-flash/        ← firmware zip + venv (optional reflash)
~/meshcore-flash/          ← alternate Heltec firmware
~/bin/backup-station.sh
~/backups/station/
```

---

## 17. External documentation

| Component | Docs |
|-----------|------|
| ACARS Hub / sdr-enthusiasts images | https://github.com/sdr-enthusiasts |
| acars_router | image env `AR_*` (see `docker inspect acars_router`) |
| gr-iridium | https://github.com/muccc/gr-iridium |
| radiosonde_auto_rx | https://github.com/projecthorus/radiosonde_auto_rx |
| SondeHub tracker | https://tracker.sondehub.org/ |
| Airframes | https://airframes.io / feed hostnames above |
| ADSb-Vue | https://github.com/jrsphoto/ADSb-Vue |
| MeshMonitor | https://github.com/yeraze/meshmonitor |
| StarTech PEXUSB3S44V | https://www.startech.com/en-us/cards-adapters/pexusb3s44v |

---

## 18. Changelog (station ops)

| Date | Change |
|------|--------|
| 2026-08-29 | sdrmap.org feeder: `sdrmap` sidecar (1090 Beast + MLAT + auto_rx logs) + shipfeeder AIS. Creds `FEEDER_SDRMAP_*` in `.env`. ACARS/VDL2/HFDL/IRDM/978 not accepted by their feeder. |
| 2026-08-24 | VHF R2 mux is ACARS+VDL2 only (ka9q channelizer, no ATC). Same LO 138.0 / 20 MS/s real. |
| 2026-08-24 | AIS antenna: **1/4-wave ground plane** on 162 MHz (replaced HYS low-profile marine). Still no 162 filter or LNA. Tuner BW **192 kHz**. |
| 2026-08-23 | StarTech USB **in production** — docs had lagged on 2026-08-17 `HC died` / Intel-only. Live: Renesas hosts up, no `HC died` this boot (since 2026-08-19). |
| 2026-08-19 | Per-SDR USB hosts: 1090 DX+FEC2; AIS tuner BW set to 1 MHz (intended as sample rate — reverted 2026-08-24); Iridium `-d 16` (keep `pack=1`, `-D 8` — `pack=0`/`-D 16` invalid or lose samples). VHF gains unchanged (A/B locked). |
| 2026-08-17 | Both StarTech USB hosts seated (4-port PEG + 2-port PCH, SATA on both); Heltec on front USB-C; Renesas `renesas_usb_fw.mem` 2.0.2.6 staged in `host/` for `/lib/firmware` + initramfs |
| 2026-08-13 | RX888 also skims FT8/FT4/WSPR (same radiod); map layers + /hf.html; PSK Reporter as WP-KMCO/EL98ho |
| 2026-08-18 | `sdr-watch` timer + udev: auto-reopen any live decoder after USB reset / stale handle (Iridium 100% loss was the canary) |
| 2026-08-13 | Boot hole: usbfs reset to 16 (modprobe.d ignored). Fix: grub + tmpfiles + usbfs.service Before=docker + rx888 systemd + watchdog |
| 2026-08-13 | StarTech removed until 3D-printed half-height brackets; 2nd PEXUSB3S44V inbound ~3 days |
| 2026-08-10 | Host disk moved to Dell OptiPlex 5060 (i5-8500, 32 GiB); laptop hostname string not published |
| 2026-08-10 | StarTech PEXUSB3S44V present (SATA power); rear bracket pending; SDRs on mobo USB |
| 2026-08-10 | USBFS unlimited via live sysfs + persist (multi-receiver restart) |
| 2026-08-10 | Netdata removed; portal Netdata card removed; graphs1090 is system/decode graphs |
| 2026-08-10 | Home cleanup: xng configs/logs, bare-metal `~/acars/`, Pi ADS-B migration zips |
| 2026-08-10 | u-blox 7 GPS on `/dev/ttyACM0` + gpsd; auto_rx still fixed-position |
| 2026-08-10 | UAT RTL serial EEPROM → `978`; Airspy map corrected: 2× Mini + 1× R2 + HF+ |
| 2026-08-08 | Prior full verification of Docker multi-mode + ADS-B cutover from Pi |

---

*End of architecture reference. When hardware serials, gains, or feed targets change, update this file and `.env` / `irdm.conf` / `station.cfg` in the same change.*
