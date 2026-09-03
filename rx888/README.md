# RX888mk2 — WP-KMCO

| | |
|--|--|
| Hardware | RX888 **mk2** (LTC2208 + FX3, HF direct-sample) |
| USB serial | `0009042501933917` |
| Bootloader | `04b4:00f3` Cypress WestBridge (USB **2.0 only**) |
| After firmware | `04b4:00f1` **RX888mk2** / `sdr prototypes` (USB **3.0 SuperSpeed**) |
| Firmware | ringof `SDDC_FX3.img` **v0.1.0** (RAM; lost on unplug) |
| Image | `rx888_tools/firmware/SDDC_FX3.img` |

The FX3 bootloader always enumerates as USB 2. After the image loads, the
same physical port reappears as SuperSpeed on the companion bus (`2-7` on
the OptiPlex motherboard xHCI). It was already in a USB 3 jack.

## Load firmware (no sudo if bootloader node is `plugdev`)

```bash
cd ~/acars-hub/rx888/rx888_tools
./fx3_cmd load firmware/SDDC_FX3.img
```

Confirm: `lsusb -d 04b4:00f1` should show `RX888mk2`.

## Host install (already applied)

udev perms, `/usr/local` tools (`fx3_cmd`, `rx888_stream`), and a
systemd oneshot that reloads firmware when the bootloader (`00f3`)
appears — so a cable move or reboot comes back as `00f1`. Sources live
under `rx888_tools/` and `udev/` if a new box needs it recreated.

Then: `fx3_cmd test` and `fx3_cmd stats`.

## Do not

- Do **not** run `LimeUtil` / bladeRF flash. Soapy Lime can mis-see the
  FX3 bootloader as a LimeSDR.
- Do **not** program FX3 EEPROM unless we decide to persist the image.

## HFDL (all published channels)

ka9q-radio at 64.8 Msps + 13 `dumphfdl` band instances (system table 52,
plus Reykjavik 3900 kHz). Output is JSON UDP into `acars_router:5556`
as `WP-KMCO-HFDL`. Do **not** also send from dumphfdl to Airframes —
the router already does.

```bash
bash ~/acars-hub/rx888/start-rx888-hfdl.sh      # parks docker dumphfdl (HF+ gone)
tail -f ~/acars-hub/rx888/run/logs/hfdl.log
bash ~/acars-hub/rx888/stop-rx888-hfdl.sh
```

Boot order: `usbfs-memory.service` (unlimited USBFS) → docker SDR containers → RX888 firmware udev → **enabled** `rx888-radiod` + `rx888-pcmrecord`. `sdr-watch` (system timer + udev, not the user timer) + `rx888-hfdl-watch.timer` call `health-fix.sh` if the farm is down after a cable move. `run-radiod.sh` treats `No rx888 data` as a crash so systemd `Restart=always` fires.

First `radiod` start builds FFTW wisdom (can take several minutes).
Connect the **MLA30+** to the RX888 **HF** port.
