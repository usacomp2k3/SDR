#!/bin/sh
# Disable USB runtime PM / autosuspend and keep StarTech + onboard USB
# hosts out of ASPM / D3cold. Attach on uPD720202 was dying with HC died.
# See Architecture.md (PCIe USB expansion).
set -eu

if [ -w /sys/module/usbcore/parameters/autosuspend ]; then
	printf '%s\n' -1 > /sys/module/usbcore/parameters/autosuspend || true
fi
if [ -w /sys/module/pcie_aspm/parameters/policy ]; then
	printf '%s\n' performance > /sys/module/pcie_aspm/parameters/policy || true
fi

# PCI: every USB controller, Pericom switches, 5060 root ports that hold the cards
for d in /sys/bus/pci/devices/*; do
	[ -f "$d/vendor" ] || continue
	v=$(cat "$d/vendor")
	i=$(cat "$d/device")
	cls=$(cat "$d/class" 2>/dev/null || echo)
	case "$v:$i" in
	0x1912:0x0014|0x1912:0x0015|0x12d8:0x2608|0x8086:0x1901|0x8086:0xa330)
		touch=1 ;;
	*)
		touch=0 ;;
	esac
	case "$cls" in
	0x0c03*) touch=1 ;;
	esac
	if [ "$touch" = 1 ]; then
		echo on > "$d/power/control" 2>/dev/null || true
		echo 0 > "$d/d3cold_allowed" 2>/dev/null || true
	fi
done

# USB: no autosuspend on hubs or devices already present
for d in /sys/bus/usb/devices/*; do
	[ -e "$d/power/control" ] || continue
	echo on > "$d/power/control" 2>/dev/null || true
	[ -e "$d/power/autosuspend" ] && echo -1 > "$d/power/autosuspend" 2>/dev/null || true
	[ -e "$d/power/autosuspend_delay_ms" ] && echo -1 > "$d/power/autosuspend_delay_ms" 2>/dev/null || true
done
