Antenna's:
Iridium - HC610
1090 - adsbexchange 5.5dBi 1090/978 N-Type Female Antenna - 26-inch
978 - Signalplus 1090MHz ADS-B Antenna-Omni Fiberglass Antenna 12dbi 45inch
915 - Bingfu Lora Antenna 915mhz 4dbi
400 - UHF Fiberglass Mobile Radio Antenna, 17" 400-470mhz GMRS Base Antenna
162 - 1/4-wave ground plane (162 MHz AIS)
118-137 - 
HF - MLA30+

Filters:
Iridium - Built into HC610 antenna 
1090 - Lothar
978 - Lothar
915 - 
400 - Lothar
162 - 
118-137 -Lothar
HF - Built into MLA30+ antennaa 

Amplifiers:
Iridium - Built into HC610 antenna - Bias-T powered
1090 - Noolelec Lana - USB Powered
978 - Noolelec Lana - USB Powered
915 - None
400 - None
162 - None
118-137 - None
HF - Built into MLA30+ antennaa - USB powered

 
Bias-T Injectors:
Iridium - Taidacent DC Feed Bias Tee RF Microwave Bias Active Antenna Power Supply Mains DC-Blocker 10-6000MHz Amplifier DC Bias
1090 - None
978 - None
915 - None
400 - None
162 - None
118-137 - None
HF - None


Splitter:
Iridium - None
1090 - None
978 - None
915 - None
400 - None
162 - None
118-137 - none (R2 takes Lothar cavity; 2-way splitter removed)
HF - None

SDR's:
Iridium - Airspy Mini - Serial 35AC63DC2D8E234F
1090 - Airspy Mini - Serial 10A862DC34914863
978 - RTL-SDR V3 - Serial 978
915 - Heltec ESP32 LoRa 32 V4 - Serial A4:CB:8F:A7:84:E0
400 - RTL SDR V4 - Serial 402
162 - RTL-SDR V3 - Serial 162
118-137 - ACARS+VDL2 - Airspy R2 - Serial 35AC63DC2D86554F
HF - RX888mk2 - Serial 0009042501933917 (HFDL, 64.8 Msps all-channel)

Software:
Iridium - irdm (gr-iridium) + acars_router + acarshub :8080 + beam map :8888
1090 - airspy_adsb + ultrafeeder (tar1090/graphs1090 :8085) + adsbvue :24556
978 - dump978 :9780 + ultrafeeder
915 - MeshMonitor + Meshtastic
400 - radiosonde_auto_rx :5000
162 - shipfeeder (AIS-Catcher) :8090
118-137 - host ka9q radiod (R2) + acarsdec + dumpvdl2 + acars_router + acarshub :8080
HF - host radiod (ka9q) + 13x dumphfdl + acars_router + acarshub :8080

Aggregators:
Iridium - Airframes feed.airframes.io:5590
1090 - FlightAware, FR24, AirNav Radar, OpenSky, PlaneFinder, plane.watch, ADSBHub, sdrmap + community ultrafeeder
978 - FR24 UAT + same ultrafeeder aggregators as 1090
915 - None
400 - SondeHub (WP-KMCO-SONDE), sdrmap
162 - Airframes, aiscatcher.org, BoatBeacon, ShipFinder, MarineTraffic, MyShipTracking, ShipXplorer, VesselFinder, AISHub, sdrmap
118-137 - ACARS - Airframes :5550, ACARS Drama, AVDelphi, FlightDeck, adsb.lol, ADSBItalia
118-137 - VDL2 - Airframes :5553, ACARS Drama, AVDelphi, FlightDeck, adsb.lol
HF - Airframes :5556, adsb.lol

Future: 
144mhz for APRS
50Mhz for FT8 & WSPR
1525mhz for Inmarsat ACars/Aero
Add NOAA polar satellites to 137mhz

Unused:
Airspy HF+ Discovery - Serial 2F52FF5DE72635E8
RTLSDR V4 - Serial TBD

## USB devices

Source of truth for the portal SDR / USB page (`/sdr.html`). Add a row when you plug something in.

| Role | Hardware | Serial | VID:PID | Kind | Decoder | Path | Notes |
|------|----------|--------|---------|------|---------|------|-------|
| Iridium | Airspy Mini | 35AC63DC2D8E234F | 1d50:60a1 | radio | irdm | | 6 MS/s, ~1619–1625 |
| 1090 | Airspy Mini | 10A862DC34914863 | 1d50:60a1 | radio | airspy_adsb | | |
| 978 | RTL-SDR V3 | 978 | 0bda:2838 | radio | dump978 | | |
| 400 | RTL-SDR V4 | 402 | 0bda:2838 | radio | radiosonde_auto_rx | | |
| 162 | RTL-SDR V3 | 162 | 0bda:2838 | radio | shipfeeder | | |
| 118-137 VHF | Airspy R2 | 35AC63DC2D86554F | 1d50:60a1 | radio | vhf-r2 | | ka9q ACARS+VDL2 |
| unused VDL2 RTL | RTL-SDR V4 | 129 | 0bda:2838 | unused | | | was VDL2 |
| HF | RX888 mk2 | 0009042501933917 | 04b4:00f1 | radio | radiod | | alt VID:PID 04b4:00f3 (bootloader) |
| 915 | Heltec ESP32 LoRa 32 V4 | A4:CB:8F:A7:84:E0 | 303a:1001 | mesh | meshtastic | | front USB-C |
| GPS | u-blox 7 | | 1546:01a7 | gps | gpsd | | /dev/ttyACM* |
| 1090 LNA | Noolelec Lana | | | amp | | | USB power only (no data interface) |
| 978 LNA | Noolelec Lana | | | amp | | | USB power only (no data interface) |
| HF amp | MLA30+ | | | amp | | | USB power only (no data interface) |
| front hub | Terminus USB 2 hub | | 1a40:0101 | hub | | 1-5 | 1090+978 chain |
| front hub | Genesys USB 2.1 hub | | 05e3:0610 | hub | | 1-5.1 | |
| front hub | Genesys USB 2.1 hub | | 05e3:0610 | hub | | 1-5.1.2 | 1090 Mini + 978 RTL |
| aux hub | Genesys USB 2.1 hub | | 05e3:0610 | hub | | 1-6 | |
| aux hub | Genesys USB 2.1 hub | | 05e3:0610 | hub | | 1-6.2 | |
| front hub SS | Genesys USB 3.1 hub | | 05e3:0626 | hub | | 2-1 | SuperSpeed companion of 1-5.1 |
| aux hub SS | Genesys USB 3.1 hub | | 05e3:0626 | hub | | 2-1.2 | SuperSpeed companion of 1-6.2 |
| unused | Airspy HF+ Discovery | 2F52FF5DE72635E8 | 03eb:800c | unused | | | parked spare |
| unused | RTL-SDR V4 | TBD | 0bda:2838 | unused | | | serial not flashed yet |