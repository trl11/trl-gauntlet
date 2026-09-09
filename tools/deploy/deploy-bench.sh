#!/bin/sh
#
# Put the release in dist/ on a bench and leave it serving.
#
#     tools/deploy/deploy-bench.sh trl@blinky [directory]
#
# `make deploy BENCH=trl@blinky` is the same thing. The directory defaults to
# ~/gauntlet on the bench.
#
# This is the whole deployment: what a bench needs is the AppImage and the
# scripts beside it, and `install-service.sh` on the far side turns those into
# a service that comes back after a reboot. Rerunning it on a bench that is
# already deployed is how that bench is updated.
#
# The host setup is not run here. It needs root on the bench, which means a
# password prompt this cannot answer, so everything it installs is copied over
# and the command to run it is printed at the end.

set -eu

BENCH=${1:-}
REMOTE_DIR=${2:-gauntlet}

HERE=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
DIST=$HERE/dist

say() {
	echo "==> $*"
}

note() {
	echo "    $*"
}

fail() {
	echo "deploy-bench: $*" >&2
	exit 1
}

[ -n "$BENCH" ] || fail "name the bench: $0 user@host [directory]"

# The AppImage is what is served; the rest is what turns it into a service and
# what the operator reads. The deb, the wheels and the image are for other
# ways of installing and are deliberately not sent.
sent="README.txt blinky.png gauntlet-homepage.service gauntlet.service homepage.html install-service.sh polkit serve-gauntlet.sh serve-homepage.py setup-bench.sh setup-host.sh tmpfiles 60-gauntlet-unprivileged-ports.conf 99-gauntlet-instruments.rules"

appimage=$(ls "$DIST"/gauntlet-*.AppImage 2>/dev/null | head -1 || true)
[ -n "$appimage" ] || fail "no gauntlet-*.AppImage in $DIST; run make build first"
for file in $sent; do
	# `polkit` and `tmpfiles` are directories rather than files, so both
	# shapes count.
	[ -s "$DIST/$file" ] || [ -d "$DIST/$file" ] ||
		fail "$DIST/$file is missing; run make build first"
done

say "sending $(basename "$appimage") and the bench scripts to $BENCH:$REMOTE_DIR"
ssh "$BENCH" "mkdir -p $REMOTE_DIR"
# Copied by name from inside dist/ rather than as a directory: dist/ also holds
# the deb, the wheels and the server image, and a bench has no use for another
# two hundred megabytes of them. The names are the fixed list above, so leaving
# the expansion unquoted is what passes them as separate arguments.
cd "$DIST"
rsync -a --info=progress2 $sent "$(basename "$appimage")" "$BENCH:$REMOTE_DIR/"

say "installing the service on $BENCH"
ssh "$BENCH" "cd $REMOTE_DIR && ./install-service.sh"

echo
say "deployed"
note "the host setup was copied but not run; it needs root there:"
note "  ssh -t $BENCH 'sudo $REMOTE_DIR/setup-host.sh'"
note "that installs the udev rules, raises the usbfs buffer limit, and fetches"
note "the vendor code for whichever instruments are on the bus"
