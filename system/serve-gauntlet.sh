#!/bin/sh
#
# Serve Gauntlet headlessly, out of the AppImage beside this script.
#
#     ./serve-gauntlet.sh [gauntlet serve arguments]
#
# The desktop bundle is a window and a backend for it. A bench left running as
# a rig wants only the backend: `gauntlet.service` runs this, and the operator
# opens the UI from their own machine.
#
# The AppImage carries its own Python, every suite and every campaign, so
# nothing here is installed. It is unpacked once into a cache directory and
# the interpreter inside is run directly. Electron never starts, so the host
# needs no display, no graphical session and no one logged in.

set -eu

HERE=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

fail() {
	echo "serve-gauntlet: $*" >&2
	exit 1
}

# The bundle to serve: named outright, or the one AppImage beside this script.
appimage=${GAUNTLET_APPIMAGE:-}
if [ -z "$appimage" ]; then
	for candidate in "$HERE"/gauntlet-*.AppImage; do
		[ -f "$candidate" ] || continue
		appimage=$candidate
		break
	done
fi
[ -n "$appimage" ] || fail "no gauntlet-*.AppImage beside $HERE, and GAUNTLET_APPIMAGE names none"
[ -x "$appimage" ] || fail "$appimage is not executable; chmod +x it"

# Keyed by modification time as well as name, so redeploying over a bundle of
# the same version unpacks the new one rather than serving the old one again.
cache=${XDG_CACHE_HOME:-$HOME/.cache}/gauntlet
stamp=$(basename "$appimage")-$(stat -c %Y "$appimage")
unpacked=$cache/$stamp

if [ ! -x "$unpacked/resources/runtime/bin/python3" ]; then
	mkdir -p "$cache"
	# --appimage-extract writes squashfs-root into the working directory, so
	# it runs somewhere of its own and the result is moved into place. A half
	# written directory is therefore never one this script would go on to run.
	work=$(mktemp -d "$cache/.unpacking.XXXXXX")
	(cd "$work" && "$appimage" --appimage-extract >/dev/null)
	rm -rf "$unpacked"
	mv "$work/squashfs-root" "$unpacked"
	rmdir "$work"

	# Each bundle is a couple of hundred megabytes. Now that this one is
	# unpacked and about to be served, the ones it replaces are just disk.
	for old in "$cache"/*; do
		[ -e "$old" ] || continue
		[ "$old" = "$unpacked" ] && continue
		rm -rf "$old"
	done
fi

# Where the packaged desktop app puts the same three things, so the service and
# the app on this machine read one run history rather than diverging into two.
export GAUNTLET_DATA_DIR=${GAUNTLET_DATA_DIR:-$HOME/.config/gauntlet}
export GAUNTLET_SUITE_PATH=${GAUNTLET_SUITE_PATH:-$unpacked/resources/suites}
export GAUNTLET_CAMPAIGN_PATH=${GAUNTLET_CAMPAIGN_PATH:-$unpacked/resources/campaigns}

# `-s` for the reason the desktop shell passes it: the bundle carries
# everything it imports, and a stray copy of one of those in ~/.local must not
# be read ahead of its own. No host or port is named here, so the application's
# own defaults apply and config.yaml in the data directory is the one place
# they are changed.
exec "$unpacked/resources/runtime/bin/python3" -s -m gauntlet serve "$@"
