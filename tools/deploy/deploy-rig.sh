#!/bin/sh
#
# Stand a new rig up from nothing, in one command.
#
#     tools/deploy/deploy-rig.sh 192.168.10.160 [user] [directory]
#
# `make deploy-rig RIG_IP=192.168.10.160` is the same thing. The user defaults
# to trl and the directory to ~/gauntlet on the rig.
#
# `deploy-bench.sh` puts a release on a bench that is already set up. This is
# the first time: it also does the four things a fresh host needs and a deploy
# alone cannot, in the one order that works.
#
#   1. lingering, before anything else. install-service.sh turns it on itself,
#      but on a fresh account that call wants a password it cannot answer, and
#      it treats the failure as fatal. Doing it here as root means the deploy
#      inside step 2 never hits that.
#   2. the deploy, which is deploy-bench.sh unchanged rather than a second copy
#      of it. On a fresh rig the landing page fails to bind port 80 here. That
#      is expected: the sysctl that allows it is in step 3, and the scripts
#      that install it only arrive in this step.
#   3. setup-bench.sh as root, which installs the packages, the udev rules, the
#      dialout and video groups, and that sysctl.
#   4. a restart of the account's systemd user manager. A process keeps the
#      groups it started with, so the services from step 2 are running without
#      the ones step 3 just granted. Restarting the units is not enough; they
#      inherit the manager. This is what makes the instruments reachable and
#      the landing page bind.
#
# Then the datasheets, which live in the data directory and are the one thing
# no bundle carries, and a check that the rig answers on both ports.
#
# Running it again on a rig that is already up is safe, and is how that rig is
# rebuilt: every step it delegates to checks before it acts. It is not free,
# though. The deploy restarts both services, and a run in flight dies with
# them, because gauntlet.service kills its whole process group. So this refuses
# to start while a run is in flight unless FORCE=1 says otherwise.
#
# Step 4 is skipped when the running service already has every group the
# account does, which is the case on any rig that has been through this once.
# Restarting the user manager drops every session the account has, and there is
# no reason to pay that when the groups are already right.

set -eu

RIG=${1:-}
RIG_USER=${2:-trl}
REMOTE_DIR=${3:-gauntlet}

HERE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)

say() {
	echo "==> $*"
}

note() {
	echo "    $*"
}

fail() {
	echo "deploy-rig: $*" >&2
	exit 1
}

[ -n "$RIG" ] || fail "name the rig: $0 <ip-or-host> [user] [directory]"

TARGET=$RIG_USER@$RIG

# Fail here rather than four steps in, where half a rig is already set up.
say "checking $TARGET answers"
ssh -o BatchMode=yes -o ConnectTimeout=10 "$TARGET" true ||
	fail "cannot ssh to $TARGET without a password; add the key first"
note "reachable"

# Only a rig that already serves can have a run in flight; a fresh one cannot.
if curl -fsS -m 3 "http://$RIG:7100/api/health" >/dev/null 2>&1; then
	in_flight=$(curl -fsS -m 5 "http://$RIG:7100/api/runs?limit=50" 2>/dev/null |
		tr '{' '\n' | grep -c '"status":"running"' || true)
	if [ "${in_flight:-0}" != 0 ]; then
		[ "${FORCE:-0}" = 1 ] ||
			fail "$in_flight run(s) in flight on $RIG. The deploy restarts the services and would kill them. Wait, or FORCE=1 to go anyway."
		note "$in_flight run(s) in flight; FORCE=1 given, so they will be killed"
	fi
fi

echo
say "step 1 of 4: lingering, so the services survive a logout and a reboot"
note "this needs root on the rig, so sudo may ask for a password"
ssh -t "$TARGET" "sudo loginctl enable-linger $RIG_USER" ||
	fail "could not enable lingering on $TARGET"

echo
say "step 2 of 4: sending the release and installing the services"
note "the landing page not binding port 80 here is expected on a fresh rig"
"$HERE/tools/deploy/deploy-bench.sh" "$TARGET" "$REMOTE_DIR"

echo
say "step 3 of 4: setting the host up"
note "packages, udev rules, the dialout and video groups, and the port 80 sysctl"
ssh -t "$TARGET" "sudo $REMOTE_DIR/setup-bench.sh" ||
	fail "setup-bench.sh failed on $TARGET"

echo
say "step 4 of 4: making sure the services have the groups the account has"

# A process keeps the groups it started with. Comparing the running service's
# set against the account's is what says whether a restart is owed, and on a
# rig that has been through this before it never is.
needs_restart=$(ssh "$TARGET" '
	pid=$(systemctl --user show gauntlet.service -p MainPID --value 2>/dev/null)
	[ -n "$pid" ] && [ -r "/proc/$pid/status" ] || { echo yes; exit 0; }
	service=$(awk "/^Groups:/{\$1=\"\"; print}" "/proc/$pid/status")
	for group in $(id -G); do
		echo "$service" | tr " " "\n" | grep -qx "$group" || { echo yes; exit 0; }
	done
	echo no
' 2>/dev/null || echo yes)

if [ "$needs_restart" = no ]; then
	note "already there; not restarting the user manager"
else
	note "restarting the user manager, which drops this account's sessions"
	# This kills every session the account has, including this one, so ssh
	# returns whatever a dropped connection returns. The wait below is what
	# says it worked.
	ssh -t "$TARGET" "sudo loginctl terminate-user $RIG_USER" || true
fi

say "waiting for the rig to answer"
answered=no
attempt=0
while [ "$attempt" -lt 60 ]; do
	if curl -fsS -m 2 "http://$RIG:7100/api/health" >/dev/null 2>&1; then
		answered=yes
		break
	fi
	attempt=$((attempt + 1))
	sleep 1
done
[ "$answered" = yes ] || fail "Gauntlet did not come back on $RIG:7100 within a minute"
note "Gauntlet is answering"

# The datasheets are state rather than release, so no bundle carries them and
# deploy-bench.sh does not send them. A rig without them still works.
if [ -d "$HERE/docs/datasheets" ]; then
	echo
	say "copying the datasheets"
	ssh "$TARGET" "mkdir -p ~/.config/gauntlet/datasheets"
	rsync -a "$HERE/docs/datasheets/" "$TARGET:.config/gauntlet/datasheets/"
	note "$(ls -1 "$HERE/docs/datasheets" | wc -l) file(s) in ~/.config/gauntlet/datasheets"
fi

echo
say "checking what the rig has"

page=no
attempt=0
while [ "$attempt" -lt 20 ]; do
	if curl -fsS -m 2 "http://$RIG/" >/dev/null 2>&1; then
		page=yes
		break
	fi
	attempt=$((attempt + 1))
	sleep 1
done

printf '    %-20s %s\n' "gauntlet" "http://$RIG:7100"
if [ "$page" = yes ]; then
	printf '    %-20s %s\n' "landing page" "http://$RIG/"
else
	printf '    %-20s %s\n' "landing page" "NOT answering on port 80"
	note "see: ssh $TARGET 'journalctl --user -u gauntlet-homepage.service -n 20'"
fi

# Named rather than counted: which instruments answered is the thing an
# operator wants to read, and a count of three says nothing about which three.
# Anchored on the "kind" that follows an instrument's name, because a command
# and a command field each carry a "name" of their own.
instruments=$(curl -fsS -m 5 "http://$RIG:7100/api/instruments" 2>/dev/null |
	tr '{' '\n' | sed -n 's/.*"name":"\([a-z0-9_]*\)","kind":.*/\1/p' | tr '\n' ' ' || true)
printf '    %-20s %s\n' "instruments" "${instruments:-none detected}"

echo
if [ "$page" = yes ]; then
	say "the rig is up"
else
	say "the rig is serving Gauntlet, but the landing page is not answering"
fi
note "stop them   ssh $TARGET 'systemctl --user stop gauntlet.service gauntlet-homepage.service'"
note "follow them ssh $TARGET 'journalctl --user -u gauntlet.service -u gauntlet-homepage.service -f'"
