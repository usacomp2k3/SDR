# VHF mux — Airspy R2

Locked station path: Lothar 118–137 cavity (no splitter) → Airspy R2 `35ac63dc2d86554f` → ka9q-radio → host `acarsdec` / `dumpvdl2`.

ka9q is the channelizer so one R2 covers POA ACARS (~129–132) and VDL2 (~136.1–137) in the same 10 MHz window. No ATC on this radio.

| | |
|--|--|
| LO | 138.0 MHz (ka9q real 20 MS/s, IF −9.4…−0.6 MHz → RF ~128.6–137.4) |
| ACARS | 17 POA channels → `acars_router:5550` as `WP-KMCO-ACARS` |
| VDL2 | 1.05 MS/s IQ @ 136.5375 → `acars_router:5555` as `WP-KMCO-VDL2` |
| Units | `vhf-r2-radiod.service` + `vhf-r2-decode.service` |
| Restart (station user) | `sudo -n systemctl restart vhf-r2-radiod.service vhf-r2-decode.service` (sudoers from refresh) |

Docker `acarsdec` / `dumpvdl2` stay parked (`legacy-usb`). Starting them steals Mini `*234f` from Iridium.

Day-to-day: `sudo bash ~/acars-hub/refresh_all_the_things.sh`
