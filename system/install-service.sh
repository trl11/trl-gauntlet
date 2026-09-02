#!/bin/sh
#
# Keep this bench serving Gauntlet, across reboots and with nobody logged in.
#
#     ./install-service.sh
#
# Run it as the operator, not as root: the unit is a systemd user unit, and it
# has to run as the account whose `dialout` membership the udev rules grant the
# instruments to. Nothing here needs sudo.
#
# It installs `gauntlet.service` beside this script into the user's own systemd
# directory, pointing it at `serve-gauntlet.sh` here, and turns lingering on so
# systemd starts the unit at boot rather than at the next login.
#
# Running it again after a redeploy is how the new bundle is picked up.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

UNIT_NAME=gauntlet.service
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
TEMPLATE=$HERE/$UNIT_NAME
SERVE=$HERE/serve-gauntlet.sh

# The landing page is the second unit, and an optional one: a bundle from
# before it existed has neither file, and a bench that only wants the backend
# is left with what it had.
PAGE_UNIT_NAME=homepage.service
PAGE_TEMPLATE=$HERE/$PAGE_UNIT_NAME
PAGE_SERVE=$HERE/serve-homepage.py
PAGE_PORT=${GAUNTLET_HOMEPAGE_PORT:-80}

say() {
	echo "==> $*"
}

note() {
	echo "    $*"
}

fail() {
	echo "install-service: $*" >&2
	exit 1
}

[ "$(id -u)" != 0 ] || fail "run me as the operator, not with sudo: $0"
[ -f "$TEMPLATE" ] || fail "$UNIT_NAME is not beside this script"
[ -x "$SERVE" ] || fail "serve-gauntlet.sh is not beside this script, or is not executable"
command -v systemctl >/dev/null 2>&1 || fail "there is no systemctl here"
systemctl --user show-environment >/dev/null 2>&1 ||
	fail "this account has no systemd user instance; log in on the console once and try again"

# Without lingering the user instance is torn down at logout and started again
# at login, so a rig that reboots unattended comes back with nothing serving.
say "keeping this account's services running when it is not logged in"
if [ "$(loginctl show-user "$(id -un)" --property=Linger --value 2>/dev/null || true)" = yes ]; then
	note "lingering already on"
else
	loginctl enable-linger "$(id -un)" ||
		fail "could not enable lingering; run: sudo loginctl enable-linger $(id -un)"
	note "lingering enabled"
fi

# ExecStart has to be an absolute path, and where the bundle was unpacked is
# only known here, so the template carries a placeholder rather than a guess.
say "installing $UNIT_NAME"
mkdir -p "$UNIT_DIR"
sed "s|@SERVE@|$SERVE|" "$TEMPLATE" > "$UNIT_DIR/$UNIT_NAME"
note "$UNIT_DIR/$UNIT_NAME"
note "runs $SERVE"

say "starting it"
systemctl --user daemon-reload
systemctl --user enable "$UNIT_NAME" >/dev/null
systemctl --user restart "$UNIT_NAME"

# The unit is up as soon as the process is, which is before uvicorn is
# listening. What an operator wants to know is whether it answers. A bench is
# not a workstation and need not have curl on it, so this asks with whichever
# of the three is here.
health_answers() {
	if command -v curl >/dev/null 2>&1; then
		curl -fsS -m 2 "$1" >/dev/null 2>&1
	elif command -v wget >/dev/null 2>&1; then
		wget -q -T 2 -O /dev/null "$1" >/dev/null 2>&1
	elif command -v python3 >/dev/null 2>&1; then
		python3 -c 'import sys,urllib.request; urllib.request.urlopen(sys.argv[1], timeout=2)' "$1" \
			>/dev/null 2>&1
	else
		return 2
	fi
}

say "waiting for it to answer"
port=$(sed -n 's/^port:[[:space:]]*//p' "${GAUNTLET_DATA_DIR:-$HOME/.config/gauntlet}/config.yaml" 2>/dev/null || true)
port=${port:-7100}
health=http://127.0.0.1:$port/api/health
answered=no
attempt=0
while [ "$attempt" -lt 30 ]; do
	status=0
	health_answers "$health" || status=$?
	if [ "$status" = 0 ]; then
		answered=yes
		break
	fi
	# Nothing here can ask, so there is no point in asking thirty times.
	if [ "$status" = 2 ]; then
		answered=unknown
		break
	fi
	attempt=$((attempt + 1))
	sleep 1
done

echo
if [ "$answered" = yes ]; then
	say "Gauntlet is serving on port $port"
	note "from this machine   http://127.0.0.1:$port"
	note "from the lab        http://$(hostname):$port"
elif [ "$answered" = unknown ]; then
	say "the service is installed; there is no curl here to ask whether it answers"
	note "check it yourself on port $port"
else
	say "the service is installed but has not answered on port $port yet"
	note "see what it is doing with: systemctl --user status $UNIT_NAME"
	note "and its output with:       journalctl --user -u $UNIT_NAME -n 50"
fi
# The landing page answers on 80 so the bare address reaches this bench. It is
# installed the same way, and separately reported: binding 80 needs the sysctl
# `setup-host.sh` installs, so this is the step that fails on a host where only
# the backend was ever set up.
if [ -f "$PAGE_TEMPLATE" ] && [ -x "$PAGE_SERVE" ]; then
	echo
	say "installing $PAGE_UNIT_NAME"
	sed "s|@SERVE@|$PAGE_SERVE|" "$PAGE_TEMPLATE" > "$UNIT_DIR/$PAGE_UNIT_NAME"
	note "$UNIT_DIR/$PAGE_UNIT_NAME"
	note "runs $PAGE_SERVE"

	systemctl --user daemon-reload
	systemctl --user enable "$PAGE_UNIT_NAME" >/dev/null
	systemctl --user restart "$PAGE_UNIT_NAME"

	say "waiting for the landing page to answer"
	page=http://127.0.0.1:$PAGE_PORT/
	page_answered=no
	attempt=0
	while [ "$attempt" -lt 15 ]; do
		status=0
		health_answers "$page" || status=$?
		if [ "$status" = 0 ]; then
			page_answered=yes
			break
		fi
		if [ "$status" = 2 ]; then
			page_answered=unknown
			break
		fi
		attempt=$((attempt + 1))
		sleep 1
	done

	if [ "$page_answered" = yes ]; then
		say "the landing page is serving on port $PAGE_PORT"
		note "from the lab        http://$(hostname)/"
	elif [ "$page_answered" = unknown ]; then
		say "the landing page is installed; there is nothing here to ask whether it answers"
	else
		say "the landing page is installed but has not answered on port $PAGE_PORT"
		note "a port below 1024 needs the sysctl that setup-host.sh installs:"
		note "  sudo $HERE/setup-host.sh"
		note "then: systemctl --user restart $PAGE_UNIT_NAME"
	fi
fi

echo
note "stop them   systemctl --user stop $UNIT_NAME $PAGE_UNIT_NAME"
note "start them  systemctl --user start $UNIT_NAME $PAGE_UNIT_NAME"
note "follow them journalctl --user -u $UNIT_NAME -u $PAGE_UNIT_NAME -f"
