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
# Two instruments need more than a rule, and both are driven by vendor code
# that cannot ship with Gauntlet. An NI acquisition unit needs National
# Instruments' own kernel driver and the gRPC device server that lets Gauntlet
# reach it from a container. An Allied Vision camera needs a GenTL transport
# layer, because the vmbpy wheel carries VmbC and no layer. Each is installed
# when its hardware is on the bus, and a host with neither downloads neither.
#
# It belongs to the host the instruments are plugged into. A container sees
# whatever the host's rules decided and cannot set it, so running this inside
# one changes nothing.
#
# Every `*.rules` file beside this script is installed, so a rule added to the
# release is picked up without this script changing, every `*.conf` goes to
# /etc/sysctl.d the same way, every `*.conf` under `tmpfiles/` goes to
# /etc/tmpfiles.d, and every `*.pkla` under `polkit/` goes to polkit's local
# authority directory.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
RULES_DIR=/etc/udev/rules.d
SYSCTL_DIR=/etc/sysctl.d
TMPFILES_DIR=/etc/tmpfiles.d
POLKIT_DIR=/etc/polkit-1/localauthority/50-local.d
# Every group an instrument node is owned by: dialout for the raw-USB nodes the
# rules regroup and for the serial adapters, video for the camera nodes.
INSTRUMENT_GROUPS="dialout video"

# National Instruments' USB vendor id. Its presence on the bus is what decides
# whether the NI driver is installed below, so a bench without NI hardware
# never downloads any of it.
NI_VENDOR=3923

# Where NI publishes the driver, and the release to take. NI-DAQmx is a kernel
# module built by dkms plus a shared library, distributed from NI's own apt
# repository; the zip below only registers that repository, which is why it is
# half a megabyte and the install that follows is not.
NI_DRIVERS_RELEASE=2026Q3
NI_DRIVERS_URL=https://download.ni.com/support/softlib/MasterRepository/LinuxDrivers${NI_DRIVERS_RELEASE}/NILinux${NI_DRIVERS_RELEASE}DeviceDrivers.zip

# The gRPC device server, which is how Gauntlet in a container reaches a driver
# installed out here: it runs beside the driver and speaks the same API over
# TCP. NI builds it against a specific glibc and publishes no package, so the
# release taken is the newest one this host can load — 2.13.0 is the last built
# against glibc 2.31 and so the newest an Ubuntu 22.04 bench can run, where a
# 24.04 one takes the current release and a tenth of the download.
NI_GRPC_DIR=/opt/ni-grpc-device-server
NI_GRPC_PORT=31763
NI_GRPC_OLD_VERSION=v2.13.0
NI_GRPC_OLD_ASSET=ni-grpc-device-server-linux-glibc2_31-x64.tar.gz
NI_GRPC_OLD_SHA256=b6ec2c7543f35d34cff8d8ed1f6dff5e1b906024b2e23e100da020c72593e94b
NI_GRPC_NEW_VERSION=v2.19.0
NI_GRPC_NEW_ASSET=ni-grpc-device-server-linux-glibc2_38-x64.tar.gz
NI_GRPC_NEW_SHA256=34d735c74914e35f286b0e14f3d90993e88f2de93a0833262c9567300481dc88

# Allied Vision's USB vendor id, which decides whether the transport layer
# below is installed. 0001 is a camera and ff01 the same camera with its
# firmware being written; either says one belongs to this bench.
AV_VENDOR=1ab2

# Where Allied Vision publishes Vimba X, and the release to take. Only the USB
# transport layer is kept: it is the whole of what VmbC is missing, and the
# rest of the package is a viewer and an SDK a bench has no use for. The same
# version and path as the devcontainer installs, so a suite developed there
# runs unchanged out here.
VIMBAX_VERSION=2025-3
VIMBAX_SHA256=a372bc5c4859dca0a2595ddc1331966d3265ffc0cbd86a19851b34f92ff6e922
VIMBAX_URL=https://downloads.alliedvision.com/VimbaX/VimbaX_Setup-${VIMBAX_VERSION}-Linux64.tar.gz
VIMBAX_CTI=/opt/vimbax/cti

fail() {
	echo "setup-host: $*" >&2
	exit 1
}

# Both vendor downloads below need curl, which a bare Ubuntu does not have.
# Installed rather than demanded: this script is the one root step a bench
# gets, and sending someone back to apt for a five hundred kilobyte package
# helps nobody.
ensure_curl() {
	command -v curl >/dev/null 2>&1 && return 0
	command -v apt-get >/dev/null 2>&1 || fail "no curl and no apt-get to install it with"
	echo "    installing curl, which the download needs"
	DEBIAN_FRONTEND=noninteractive apt-get update -qq
	DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl >/dev/null
	command -v curl >/dev/null 2>&1 || fail "curl is still not installed"
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

# What raises the usbfs buffer limit a USB3 Vision frame arrives in. A module
# parameter rather than a sysctl, so it is written by tmpfiles instead.
# Optional the same way: a release from before the camera existed ships none.
tmpfiles=$(find "$HERE/tmpfiles" -maxdepth 1 -name '*.conf' 2>/dev/null | sort)
if [ -n "$tmpfiles" ]; then
	echo "==> installing tmpfiles settings into $TMPFILES_DIR"
	mkdir -p "$TMPFILES_DIR"
	for conf in $tmpfiles; do
		install -m 644 "$conf" "$TMPFILES_DIR/"
		echo "    $(basename "$conf")"
	done
	# Applies now as well as at the next boot, for the same reason the sysctl
	# is applied now: a camera should work without the bench rebooting first.
	systemd-tmpfiles --create >/dev/null 2>&1 ||
		echo "    could not apply them now; they take effect at the next boot"
	echo "    usbfs_memory_mb = $(cat /sys/module/usbcore/parameters/usbfs_memory_mb 2>/dev/null || echo '?')"
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

# -------------------------------------------------------------- vendor code

# Is a device of this vendor on the bus. Both vendor installs below turn on
# it: a bench without the hardware downloads nothing and installs nothing.
vendor_present() {
	for device in /sys/bus/usb/devices/*; do
		[ -r "$device/idVendor" ] || continue
		[ "$(tr 'A-F' 'a-f' < "$device/idVendor")" = "$1" ] && return 0
	done
	return 1
}

# The newest gRPC device server this host's loader can run, as `version asset
# sha256`. NI publishes one build per glibc and no package, so a host older
# than the current build's glibc takes the last release built against one it
# has, rather than a binary that will not start.
ni_grpc_release() {
	minor=$(ldd --version 2>/dev/null | head -1 | awk '{print $NF}' | cut -d. -f2)
	case $minor in
	'' | *[!0-9]*) fail "cannot read this host's glibc version, so no server build can be chosen" ;;
	esac
	if [ "$minor" -ge 38 ]; then
		echo "$NI_GRPC_NEW_VERSION $NI_GRPC_NEW_ASSET $NI_GRPC_NEW_SHA256"
	elif [ "$minor" -ge 31 ]; then
		echo "$NI_GRPC_OLD_VERSION $NI_GRPC_OLD_ASSET $NI_GRPC_OLD_SHA256"
	else
		fail "glibc 2.$minor is older than any published gRPC device server"
	fi
}

if ! vendor_present $NI_VENDOR; then
	echo "==> no NI hardware on the bus, so NI-DAQmx is not installed"
	echo "    plug the chassis in and run this again to install it"
else
	echo "==> NI hardware is on the bus"
	. /etc/os-release 2>/dev/null || fail "no /etc/os-release, so this host cannot be identified"
	[ "${ID:-}" = ubuntu ] ||
		fail "NI packages its driver for Ubuntu, RHEL and openSUSE; this is ${ID:-unknown}, so install it by hand"
	ensure_curl

	work=$(mktemp -d)
	# Both downloads are large and neither is wanted afterwards.
	trap 'rm -rf "$work"' EXIT INT TERM

	if dpkg -s ni-daqmx >/dev/null 2>&1; then
		echo "    NI-DAQmx is already installed"
	else
		command -v unzip >/dev/null 2>&1 || {
			echo "    installing unzip, which the NI download needs"
			DEBIAN_FRONTEND=noninteractive apt-get install -y -qq unzip >/dev/null
		}
		echo "    fetching NI's repository registration"
		curl -fsSL -o "$work/ni-drivers.zip" "$NI_DRIVERS_URL"
		unzip -q -o "$work/ni-drivers.zip" -d "$work"
		# NI names the deb for the release and the distribution version with
		# its dot removed: 22.04 is ni-ubuntu2204-drivers-<release>.deb. It is
		# searched for rather than addressed, because some releases zip the
		# debs under a directory and some leave them at the top level.
		deb="ni-ubuntu$(echo "${VERSION_ID:-}" | tr -d .)-drivers-${NI_DRIVERS_RELEASE}.deb"
		registration=$(find "$work" -name "$deb" | head -1)
		[ -n "$registration" ] ||
			fail "NI publishes no ${NI_DRIVERS_RELEASE} driver for Ubuntu ${VERSION_ID:-unknown}"
		echo "    installing NI-DAQmx from NI's repository"
		DEBIAN_FRONTEND=noninteractive apt-get update -qq
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq "$registration" >/dev/null
		DEBIAN_FRONTEND=noninteractive apt-get update -qq
		DEBIAN_FRONTEND=noninteractive apt-get install -y -qq ni-daqmx >/dev/null
		# The kernel modules are built by dkms and are not built by the install
		# itself. Missing this is why a fresh install reports no devices.
		echo "    building the kernel modules"
		dkms autoinstall
		echo "    NI-DAQmx is installed; this host must reboot before it works"
	fi

	if [ -x "$NI_GRPC_DIR/ni_grpc_device_server" ]; then
		echo "    the gRPC device server is already installed"
	else
		set -- $(ni_grpc_release)
		echo "    fetching the gRPC device server $1"
		curl -fsSL -o "$work/ni-grpc.tar.gz" \
			"https://github.com/ni/grpc-device/releases/download/$1/$2"
		echo "$3  $work/ni-grpc.tar.gz" | sha256sum -c - >/dev/null ||
			fail "the gRPC device server download does not match its published checksum"
		mkdir -p "$NI_GRPC_DIR"
		tar xzf "$work/ni-grpc.tar.gz" -C "$NI_GRPC_DIR"
		chmod 755 "$NI_GRPC_DIR/ni_grpc_device_server"
	fi

	# Bound to every interface rather than to loopback, which is what NI ships:
	# Gauntlet in a container reaches this host across the bridge, and a server
	# on loopback would not answer it. That puts the acquisition unit on the
	# bench's network, which is the same trust boundary Gauntlet itself is on.
	echo "    writing $NI_GRPC_DIR/server_config.json"
	cat > "$NI_GRPC_DIR/server_config.json" <<JSON
{
    "address": "[::]",
    "port": $NI_GRPC_PORT,
    "security": {
        "server_cert": "",
        "server_key": "",
        "root_cert": ""
    }
}
JSON

	# A system unit, and root, unlike everything else Gauntlet installs. This
	# is not Gauntlet: it is NI's server talking to NI's kernel driver, and it
	# has to be up before any operator logs in.
	echo "    installing the ni-grpc-device-server unit"
	cat > /etc/systemd/system/ni-grpc-device-server.service <<UNIT
[Unit]
Description=NI gRPC Device Server
After=network.target

[Service]
ExecStart=$NI_GRPC_DIR/ni_grpc_device_server $NI_GRPC_DIR/server_config.json
Restart=on-failure

[Install]
WantedBy=multi-user.target
UNIT
	systemctl daemon-reload
	systemctl enable ni-grpc-device-server.service >/dev/null
	# Restarted rather than merely started: the config above is rewritten every
	# run, and a server already up would otherwise keep the one it read.
	systemctl restart ni-grpc-device-server.service
	echo "    Gauntlet reaches it with daq_serial: \"daqmx://$(hostname):$NI_GRPC_PORT\""

	rm -rf "$work"
	trap - EXIT INT TERM
fi

# -------------------------------------------------------------- Vimba X

if ! vendor_present $AV_VENDOR; then
	echo "==> no Allied Vision camera on the bus, so no transport layer is installed"
	echo "    plug the camera in and run this again to install it"
elif [ -n "$(find "$VIMBAX_CTI" -maxdepth 1 -name '*.cti' 2>/dev/null)" ]; then
	echo "==> the GenTL transport layer is already installed in $VIMBAX_CTI"
else
	echo "==> an Allied Vision camera is on the bus"
	ensure_curl

	work=$(mktemp -d)
	trap 'rm -rf "$work"' EXIT INT TERM

	echo "    fetching Vimba X $VIMBAX_VERSION"
	curl -fsSL -o "$work/vimbax.tar.gz" "$VIMBAX_URL"
	echo "$VIMBAX_SHA256  $work/vimbax.tar.gz" | sha256sum -c - >/dev/null ||
		fail "the Vimba X download does not match its published checksum"
	mkdir -p "$VIMBAX_CTI"
	# Only the USB layer and its descriptor, out of a package that is mostly a
	# viewer and an SDK. VmbC loads a layer by reading the directory, so the
	# two files landing there is the whole install.
	tar xzf "$work/vimbax.tar.gz" -C "$VIMBAX_CTI" --strip-components=2 \
		"VimbaX_${VIMBAX_VERSION}/cti/VimbaUSBTL.cti" \
		"VimbaX_${VIMBAX_VERSION}/cti/VimbaUSBTL.xml"
	chmod 644 "$VIMBAX_CTI/VimbaUSBTL.xml"
	chmod 755 "$VIMBAX_CTI/VimbaUSBTL.cti"
	echo "    installed the USB transport layer in $VIMBAX_CTI"

	rm -rf "$work"
	trap - EXIT INT TERM
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
