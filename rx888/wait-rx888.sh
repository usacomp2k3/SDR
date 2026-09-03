#!/bin/bash
# Wait until RX888mk2 is in application mode (PID 00f1). Firmware oneshot
# is udev-triggered and may still be running when we start.
set -euo pipefail
for _ in $(seq 1 45); do
  if lsusb -d 04b4:00f1 >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done
echo "RX888mk2 not in app mode (04b4:00f1) after 45s" >&2
lsusb -d 04b4: >&2 || true
exit 1
