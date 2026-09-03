# Local ADS-B / UAT feeder (migrated from adsb.im WinterPark)

## Hardware (moved from Pi)
- Airspy Mini serial `10A862DC34914863` → `airspy_adsb` (1090)
- RTL-SDR serial `00000001` → `dump978` (978 UAT)

## Data dirs
- `ultrafeeder/graphs1090` — graphs1090 / collectd (RRD history restored from adsb-feeder backup)
- `ultrafeeder/globe_history` — tar1090 heatmaps / traces (rsync from Pi if available)
- `dump978` — UAT autogain state

## Bring-up
```bash
# After radios are plugged in and Pi feeders stopped:
cd ~/acars-hub
sg docker -c 'docker compose -f docker-compose.adsb.yml up -d'
# or full stack via ~/acars-hub/refresh_all_the_things.sh
```

## Ports
- tar1090 map: http://127.0.0.1:8085/
- dump978 skyaware (optional): http://127.0.0.1:9780/
- beast out (host): 30005
