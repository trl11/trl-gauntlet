#!/bin/sh
#
# Say what a running rig is serving and whether it is behind this checkout.
#
#     .claude/skills/update-rig/rig-status.sh [trl@blinky [port]]
#
# With no arguments the rig and port come from `.rig` at the repository root,
# which is gitignored so each checkout names its own rig. A rig named here when
# there is no `.rig` yet is written to one, so the next bare run reuses it.
#
# Otherwise read-only: it asks the rig's API and the local git tree.
#
# Exit status:
#   0  the rig serves this checkout's HEAD and the tree is clean
#   1  the rig is behind, or the tree has changes HEAD does not record
#   2  the rig did not answer
#   3  a run is in flight on the rig, so deploying now would kill it

set -u

ROOT=$(git rev-parse --show-toplevel)
RIG=
PORT=
# shellcheck source=/dev/null
[ -f "$ROOT/.rig" ] && . "$ROOT/.rig"
RIG=${1:-$RIG}
PORT=${2:-${PORT:-7100}}
[ -n "$RIG" ] || { echo "usage: $0 user@host [port], or name the rig in $ROOT/.rig" >&2; exit 64; }

if [ ! -f "$ROOT/.rig" ]; then
	printf '%s\n' \
		"# The rig this checkout deploys to, read by .claude/skills/update-rig." \
		"# Shell syntax. Not committed: each checkout names its own rig." \
		"RIG=$RIG" "PORT=$PORT" >"$ROOT/.rig"
	echo "wrote      $ROOT/.rig naming $RIG on port $PORT"
fi

HOST=${RIG#*@}
API="http://$HOST:$PORT/api"

json() {
	python3 -c "import json,sys; d=json.load(sys.stdin); print($1)"
}

info=$(curl -sf -m 5 "$API/system/info") || {
	echo "rig        $HOST:$PORT did not answer"
	echo "verdict    UNREACHABLE: check the service with: ssh $RIG systemctl --user status gauntlet.service"
	exit 2
}

rig_sha=$(printf '%s' "$info" | json 'd.get("git_sha") or ""')
rig_version=$(printf '%s' "$info" | json 'd.get("gauntlet") or ""')
head_sha=$(git -C "$ROOT" rev-parse HEAD)
branch=$(git -C "$ROOT" rev-parse --abbrev-ref HEAD)
dirty=$(git -C "$ROOT" status --porcelain --untracked-files=no | wc -l | tr -d ' ')
built_sha=$(sed -n 's/^GIT_SHA: str = "\(.*\)"$/\1/p' "$ROOT/packages/gauntlet/src/gauntlet/_build_info.py" 2>/dev/null)
# A failed query is not an idle rig, so it is reported rather than read as none.
live=$(curl -sf -m 5 "$API/runs?status=starting&status=running&status=stopping&status=aborting&limit=5" |
	json '" ".join(r["run_id"] + " (" + r["suite"] + ", " + r["status"] + ")" for r in d["runs"])') ||
	live="unknown: the runs query failed"

echo "rig        $HOST:$PORT  gauntlet $rig_version  commit ${rig_sha:-unknown}"
echo "local      $branch  HEAD $head_sha  $([ "$dirty" = 0 ] && echo clean || echo "$dirty uncommitted file(s)")"
echo "dist       built from ${built_sha:-unknown}"

if [ -n "$rig_sha" ] && git -C "$ROOT" cat-file -e "$rig_sha^{commit}" 2>/dev/null; then
	behind=$(git -C "$ROOT" rev-list --count "$rig_sha..HEAD")
	ahead=$(git -C "$ROOT" rev-list --count "HEAD..$rig_sha")
	echo "behind     $behind commit(s) behind HEAD, $ahead not in HEAD"
	[ "$behind" = 0 ] || git -C "$ROOT" log --oneline "$rig_sha..HEAD" | sed 's/^/           /'
	host_files=$(git -C "$ROOT" diff --name-only "$rig_sha" HEAD -- targets/service tools/deploy)
	[ -z "$host_files" ] || { echo "host setup changed since the rig's build:"; echo "$host_files" | sed 's/^/           /'; }
else
	echo "behind     unknown: the rig's commit is not in this repository"
fi

if [ -n "$live" ]; then
	echo "in flight  $live"
	echo "verdict    BUSY: a deploy restarts the service and kills that run"
	exit 3
fi

if [ "$rig_sha" = "$head_sha" ] && [ "$dirty" = 0 ]; then
	echo "verdict    CURRENT: the rig serves HEAD"
	exit 0
fi
if [ "$rig_sha" = "$head_sha" ]; then
	echo "verdict    STALE?: same commit, but the tree has changes a build would include and the commit does not record"
else
	echo "verdict    BEHIND: deploy to bring the rig to HEAD"
fi
exit 1
