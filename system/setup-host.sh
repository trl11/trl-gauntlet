#!/bin/sh
#
# Set up a Linux host to run Gauntlet's instruments.
#
#     sudo ./setup-host.sh
#
# Gauntlet claims some instruments over raw USB, through usbfs, whose device
# nodes are root:root 0664 by default — enough to read a device's descriptors,
# not enough to talk to it. This installs the udev rules that hand those nodes
# to a group, and puts the invoking user in that group.
#
# It belongs to the host the instruments are plugged into. A container sees
# whatever the host's rules decided and cannot set it, so running this inside
# one changes nothing.
#
# Every `*.rules` file beside this script is installed, so a rule added to the
# release is picked up without this script changing.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RULES_DIR=/etc/udev/rules.d
GROUP=dialout

fail() {
	echo "setup-host: $*" >&2
	exit 1
}

[ "$(id -u)" = 0 ] || fail "run me as root: sudo $0"
command -v udevadm >/dev/null 2>&1 || fail "no udevadm on this host, so it has no udev to configure"

rules=$(find "$HERE" -maxdepth 1 -name '*.rules' | sort)
[ -n "$rules" ] || fail "no .rules file beside $0"

echo "==> installing udev rules into $RULES_DIR"
mkdir -p "$RULES_DIR"
for rule in $rules; do
	install -m 644 "$rule" "$RULES_DIR/"
	echo "    $(basename "$rule")"
done

echo "==> reloading udev"
udevadm control --reload-rules
# Applies the new rules to what is already plugged in. Without it a device
# attached before this ran keeps the ownership it was given at the time.
udevadm trigger --subsystem-match=usb --action=add

# The rules hand the nodes to a group, which does nothing for a user who is not
# in it. SUDO_USER is who asked for this; under a root login there is nobody
# else to add.
user=${SUDO_USER:-}
if [ -n "$user" ] && [ "$user" != root ]; then
	if id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$GROUP"; then
		echo "==> $user is already in $GROUP"
	else
		echo "==> adding $user to $GROUP"
		usermod -aG "$GROUP" "$user"
		echo "    $user must log out and back in before this takes effect"
	fi
else
	echo "==> no user to add to $GROUP (run under sudo to add yours)"
fi

# What the rules cover, and whether it worked. A vendor id read back out of the
# rules rather than repeated here, so this reports on whatever was installed.
vendors=$(grep -ho 'ATTRS{idVendor}=="[0-9a-fA-F]*"' $rules |
	sed 's/.*"\(.*\)"/\1/' | tr 'A-F' 'a-f' | sort -u)

echo "==> instruments these rules cover"
found=0
for device in /sys/bus/usb/devices/*; do
	[ -r "$device/idVendor" ] || continue
	vendor=$(cat "$device/idVendor" | tr 'A-F' 'a-f')
	echo "$vendors" | grep -qx "$vendor" || continue
	bus=$(cat "$device/busnum" 2>/dev/null) || continue
	number=$(cat "$device/devnum" 2>/dev/null) || continue
	node=$(printf '/dev/bus/usb/%03d/%03d' "$bus" "$number")
	[ -e "$node" ] || continue
	found=$((found + 1))
	product=$(cat "$device/idProduct" 2>/dev/null || echo "????")
	serial=$(cat "$device/serial" 2>/dev/null || echo "")
	owner=$(stat -c '%U:%G %a' "$node" 2>/dev/null || echo "?")
	printf '    %s:%s  %s  %s  %s\n' "$vendor" "$product" "$node" "$owner" "$serial"
done
if [ "$found" = 0 ]; then
	echo "    none plugged in — that is fine, the rules apply when one is"
fi

echo
echo "Done. Instruments plugged in from now on are reachable without this rerunning."
