#!/bin/sh
#
# Prepare a Linux host to run the Gauntlet AppImage against real instruments.
#
#     sudo ./setup-bench.sh
#
# `setup-host.sh` beside this script hands the raw-USB instrument nodes to a
# group. That is one of four things a fresh bench needs, and this runs all of
# them:
#
#   * libfuse2, which the AppImage mounts itself through. Without it the
#     bundle only starts under --appimage-extract-and-run, which unpacks two
#     hundred megabytes into /tmp on every launch.
#   * the udev rules and group membership, delegated to setup-host.sh.
#   * brltty released from the USB serial adapter. brltty ships a udev rule
#     claiming CH340 adapters as braille displays, so a bench supply on one
#     gets no /dev/ttyUSB node and Gauntlet reports no PSU on the bench.
#   * iperf3, which the ethernet and LAN controller suites run lab-side.
#
# Every step checks before it acts, so running this on a host that is already
# set up changes nothing and reports what it found.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

# The USB serial adapters brltty claims that an instrument is also likely to
# be on. Each entry is a vendor:product as lsusb prints it.
BRLTTY_SERIAL_IDS="1a86:7523 1a86:7522 1a86:5523 0403:6001 10c4:ea60 067b:2303"

steps_changed=0

say() {
	echo "==> $*"
}

note() {
	echo "    $*"
}

fail() {
	echo "setup-bench: $*" >&2
	exit 1
}

changed() {
	steps_changed=$((steps_changed + 1))
}

[ "$(id -u)" = 0 ] || fail "run me as root: sudo $0"

# apt is how the two packages below are installed. A host without it can still
# have them, so this only matters when something is actually missing.
has_apt() {
	command -v apt-get >/dev/null 2>&1
}

apt_updated=0

install_package() {
	package=$1
	has_apt || fail "$package is missing and there is no apt-get here to install it"
	if [ "$apt_updated" = 0 ]; then
		say "refreshing the package lists"
		DEBIAN_FRONTEND=noninteractive apt-get update -qq
		apt_updated=1
	fi
	say "installing $package"
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$package" >/dev/null
	changed
}

# ---------------------------------------------------------------- libfuse2

# The library rather than the package name, so a host that got it some other
# way is not made to install Debian's.
say "checking libfuse2, which the AppImage mounts itself through"
if ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2'; then
	note "already present"
else
	install_package libfuse2
	ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2' ||
		fail "libfuse2 installed but libfuse.so.2 is still not on the loader path"
fi

# ------------------------------------------------------------------ iperf3

say "checking iperf3, which the ethernet suites measure with"
if command -v iperf3 >/dev/null 2>&1; then
	note "already present: $(iperf3 --version 2>&1 | head -1)"
else
	install_package iperf3
fi
note "the unit under test needs iperf3 too; this installs it only here"

# ------------------------------------------------------------- udev rules

# Delegated rather than duplicated: setup-host.sh owns the rules and the
# group, and reports the instruments they cover.
say "installing the instrument udev rules"
[ -x "$HERE/setup-host.sh" ] || fail "setup-host.sh is not beside this script"
"$HERE/setup-host.sh"

# ------------------------------------------------------------------ brltty

# brltty claims the adapter through udev the moment it is plugged in, so
# masking the unit is what stops it happening again; killing the running
# daemon and re-enumerating is what fixes the adapter already plugged in.
say "checking brltty, which claims USB serial adapters as braille displays"
if ! command -v brltty >/dev/null 2>&1 && ! systemctl list-unit-files 2>/dev/null | grep -q '^brltty'; then
	note "not installed, nothing to release"
else
	if [ "$(systemctl is-enabled brltty-udev.service 2>/dev/null || true)" = masked ]; then
		note "brltty-udev.service already masked"
	else
		note "masking brltty-udev.service"
		systemctl mask brltty-udev.service >/dev/null 2>&1 || true
		changed
	fi

	# A daemon started before the mask still holds the adapter through usbfs,
	# and the interface stays driverless until it lets go.
	if pgrep -x brltty >/dev/null 2>&1; then
		note "stopping the running brltty daemon"
		pkill -x brltty || true
		sleep 2
		changed
	fi
fi

# ------------------------------------------ USB serial adapters without a tty

# An adapter whose interface has no driver is one brltty took and released, or
# one plugged in while it still held the rule. Re-enumerating lets the kernel's
# usbserial driver probe it and create the /dev/ttyUSB node.
say "checking USB serial adapters for a missing tty"
rebound=0
for device in /sys/bus/usb/devices/*; do
	[ -r "$device/idVendor" ] || continue
	[ -r "$device/idProduct" ] || continue
	id="$(cat "$device/idVendor"):$(cat "$device/idProduct")"
	echo "$BRLTTY_SERIAL_IDS" | tr ' ' '\n' | grep -qx "$id" || continue

	name=$(basename "$device")
	# The tty shows up under the interface directory once a driver owns it.
	# Globbed rather than found, because the entries here are symlinks into
	# /sys/devices and find would not descend them.
	has_tty=0
	for candidate in "$device"/*:*/ttyUSB* "$device"/*:*/tty/ttyUSB*; do
		if [ -e "$candidate" ]; then
			has_tty=1
			break
		fi
	done
	if [ "$has_tty" = 1 ]; then
		note "$id ($name) already has its tty"
		continue
	fi

	note "$id ($name) has no tty, re-enumerating it"
	printf %s "$name" > /sys/bus/usb/drivers/usb/unbind 2>/dev/null || true
	sleep 1
	printf %s "$name" > /sys/bus/usb/drivers/usb/bind 2>/dev/null || true
	sleep 2
	rebound=$((rebound + 1))
	changed
done
if [ "$rebound" = 0 ]; then
	note "nothing needed re-enumerating"
fi

# ------------------------------------------------------------------ report

echo
say "what this host now has"
printf '    %-24s %s\n' "libfuse2" \
	"$(ldconfig -p 2>/dev/null | grep -q 'libfuse\.so\.2' && echo yes || echo NO)"
printf '    %-24s %s\n' "iperf3" \
	"$(command -v iperf3 >/dev/null 2>&1 && echo yes || echo NO)"
printf '    %-24s %s\n' "brltty-udev masked" \
	"$([ "$(systemctl is-enabled brltty-udev.service 2>/dev/null || true)" = masked ] && echo yes || echo "n/a")"

serial=$(ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | tr '\n' ' ' || true)
printf '    %-24s %s\n' "serial ports" "${serial:-none}"

video=$(ls /dev/video* 2>/dev/null | tr '\n' ' ' || true)
printf '    %-24s %s\n' "video nodes" "${video:-none}"

echo
if [ "$steps_changed" = 0 ]; then
	echo "Nothing needed changing. This host was already set up."
else
	echo "Done. $steps_changed step(s) changed something."
fi
echo "Run the AppImage with ./gauntlet-<version>.AppImage — it needs no install."
