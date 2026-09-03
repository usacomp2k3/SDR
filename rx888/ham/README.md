# HF ham skimmer (same RX888 as HFDL)

FT8, FT4, and WSPR USB channels on the existing 64.8 Msps radiod.
Spots with Maidenhead grids go on the unified map and
http://127.0.0.1:8880/hf.html

| Feed | Status |
|------|--------|
| [PSK Reporter](https://pskreporter.info/pskmap.html) | On, reporter `KQ4ORY` / `EL98ho` |
| [WSPRnet](https://wsprnet.org/) | On (`HF_HAM_CALL=KQ4ORY`) |

Callsign is in `ham.env`. Restart ham senders after edits: `bash stop-ham.sh && bash start-ham.sh`.

6 m is out of the RX888 Nyquist (32.4 MHz). JS8 / HF APRS / FST4W not in this pass.
