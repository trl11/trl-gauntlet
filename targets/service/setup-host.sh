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
# The rest need no rule, because the kernel's own driver already grouped them:
# usbserial gives a bench supply a /dev/ttyUSB* owned by dialout, and uvcvideo
# gives a camera a /dev/video* owned by video. Those want the membership alone,
# which is why the groups granted here are more than the rules mention.
#
# It belongs to the host the instruments are plugged into. A container sees
# whatever the host's rules decided and cannot set it, so running this inside
# one changes nothing.
#
# Every `*.rules` file beside this script is installed, so a rule added to the
# release is picked up without this script changing, every `*.conf` goes to
# /etc/sysctl.d the same way, and every `*.pkla` under `polkit/` goes to
# polkit's local authority directory.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RULES_DIR=/etc/udev/rules.d
SYSCTL_DIR=/etc/sysctl.d
POLKIT_DIR=/etc/polkit-1/localauthority/50-local.d
# Every group an instrument node is owned by: dialout for the raw-USB nodes the
# rules regroup and for the serial adapters, video for the camera nodes.
INSTRUMENT_GROUPS="dialout video"

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

# What lets the landing page's user unit bind port 80, which an ordinary
# account cannot otherwise do. Optional: a release from before the page existed
# ships no .conf, and a bench that only serves the backend needs none.
sysctls=$(find "$HERE" -maxdepth 1 -name '*.conf' | sort)
if [ -n "$sysctls" ]; then
	echo "==> installing sysctl settings into $SYSCTL_DIR"
	mkdir -p "$SYSCTL_DIR"
	for conf in $sysctls; do
		install -m 644 "$conf" "$SYSCTL_DIR/"
		echo "    $(basename "$conf")"
	done
	# Applies now as well as at the next boot, so the page can be started
	# straight after this without rebooting the bench.
	sysctl --system >/dev/null 2>&1 || echo "    could not apply them now; they take effect at the next boot"
	echo "    net.ipv4.ip_unprivileged_port_start = $(sysctl -n net.ipv4.ip_unprivileged_port_start 2>/dev/null || echo '?')"
fi

# What lets the operator power the bench down from the UI. logind allows that
# without a password only for a user with an active local session, and a rig
# serves from a lingering user manager that has none. Optional, like the
# sysctl: a release from before this existed ships no polkit/ directory.
polkit_rules=$(find "$HERE/polkit" -maxdepth 1 -name '*.pkla' 2>/dev/null | sort)
if [ -n "$polkit_rules" ]; then
	echo "==> installing polkit rules into $POLKIT_DIR"
	mkdir -p "$POLKIT_DIR"
	for rule in $polkit_rules; do
		install -m 644 "$rule" "$POLKIT_DIR/"
		echo "    $(basename "$rule")"
	done
	# polkit re-reads the directory itself, so there is nothing to reload. A
	# service already running keeps its refusal cached only as long as its
	# current call, so the next press of the button is the test.
	echo "    the rig can now be powered off from its UI"
fi

# The rules hand the nodes to a group, which does nothing for a user who is not
# in it. SUDO_USER is who asked for this; under a root login there is nobody
# else to add.
user=${SUDO_USER:-}
if [ -n "$user" ] && [ "$user" != root ]; then
	added=0
	for group in $INSTRUMENT_GROUPS; do
		if ! getent group "$group" >/dev/null 2>&1; then
			echo "==> no $group group on this host, so nothing to add $user to"
		elif id -nG "$user" 2>/dev/null | tr ' ' '\n' | grep -qx "$group"; then
			echo "==> $user is already in $group"
		else
			echo "==> adding $user to $group"
			usermod -aG "$group" "$user"
			added=$((added + 1))
		fi
	done
	if [ "$added" != 0 ]; then
		echo "    $user must log out and back in before this takes effect"
		# A rig serves from a lingering systemd user manager that outlives a
		# login, and a process keeps the groups it started with, so restarting
		# the service alone leaves it without the new one.
		echo "    a rig serving through systemd needs its user manager restarted:"
		echo "      sudo loginctl terminate-user $user"
	fi
else
	echo "==> no user to add to $INSTRUMENT_GROUPS (run under sudo to add yours)"
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
