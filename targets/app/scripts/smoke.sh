#!/usr/bin/env bash
#
# Runs the packaged app with the build's runtime moved out of the way.
#
# Anything in the bundle still pointing at the tree that built it works on the
# build host and nowhere else, because the build host is the one machine where
# that path exists. An absolute shebang in a console script is exactly that,
# and it shipped once already. Moving the runtime aside is what makes the
# difference visible here rather than on the machine the app is installed on.
#
#   targets/app/scripts/smoke.sh <unpacked-app-directory>

set -euo pipefail

APP_DIR=${1:?usage: smoke.sh <unpacked-app-directory>}
APP=$(cd "$(dirname "$0")/.." && pwd)
RUNTIME=$APP/runtime
PARKED=$APP/runtime.parked-by-smoke
WORK=$(mktemp -d)
SUITE=example_sampled
PROFILE=smoke.yaml

# The runtime goes back whatever happens, including an interrupt: leaving it
# parked would break every later build in a way that looks unrelated.
#
# `mv` into a directory that already exists puts one inside the other rather
# than replacing it, and either half of the swap can find one there: the trap
# does not run when the machine goes away mid-run. Both halves refuse to move
# onto something, so a run that died leaves a runtime this one can restore
# rather than a runtime nested in a runtime that no interpreter can be found
# in.
cleanup() {
    pkill -TERM -f "$APP_DIR/gauntlet" 2>/dev/null || true
    sleep 2
    pkill -KILL -f "$APP_DIR/gauntlet" 2>/dev/null || true
    [ -d "$PARKED" ] && [ ! -d "$RUNTIME" ] && mv "$PARKED" "$RUNTIME"
    rm -rf "$WORK"
}
trap cleanup EXIT

fail() {
    echo "smoke: $1" >&2
    echo "--- app output ---" >&2
    tail -30 "$WORK/log" >&2 2>/dev/null || true
    exit 1
}

[ -x "$APP_DIR/gauntlet" ] || fail "no packaged app at $APP_DIR"
command -v xvfb-run >/dev/null || fail "xvfb-run is missing; it is in dependencies.txt"

# A runtime already parked is one an earlier run never put back. It goes back
# now, so this run parks the whole runtime rather than nesting it in that one.
if [ -d "$PARKED" ]; then
    [ -d "$RUNTIME" ] && fail "$RUNTIME and $PARKED both exist; keep one and remove the other"
    mv "$PARKED" "$RUNTIME"
    echo "==> $PARKED was left by a run that died; put back"
fi

[ -d "$RUNTIME" ] && mv "$RUNTIME" "$PARKED"
echo "==> $RUNTIME moved aside; the app has only what it packaged"

ELECTRON_DISABLE_SANDBOX=1 xvfb-run -a "$APP_DIR/gauntlet" \
    --no-sandbox --user-data-dir="$WORK/userdata" >"$WORK/log" 2>&1 &

# The port is the kernel's choice, so it is read back from what the app says
# rather than assumed.
BASE=
for _ in $(seq 1 60); do
    BASE=$(grep -o "http://127.0.0.1:[0-9]*" "$WORK/log" | head -1 || true)
    [ -n "$BASE" ] && break
    sleep 1
done
[ -n "$BASE" ] || fail "the backend never reported a port"

for _ in $(seq 1 30); do
    curl -fsS -o /dev/null "$BASE/api/health" 2>/dev/null && break
    sleep 1
done
curl -fsS -o /dev/null "$BASE/api/health" || fail "no answer from $BASE/api/health"
echo "==> serving on $BASE"

grep -q "resources/runtime" "$WORK/log" || true
SUITES=$(curl -fsS "$BASE/api/suites" | python3 -c "import json,sys; print(len(json.load(sys.stdin)['suites']))")
[ "$SUITES" -gt 0 ] || fail "the packaged app discovered no suites"
echo "==> $SUITES suites discovered"

# A Python suite, because it is a separate process that has to import
# gauntlet_sdk from the packaged runtime rather than from anything installed
# on this machine.
# The body is kept and the status read separately, because a refusal says in
# the body which part of the request it would not honour, and that is the one
# thing worth reading when this fails.
START=$WORK/start.json
CODE=$(curl -sS -X POST "$BASE/api/runs" -H 'Content-Type: application/json' \
    -d "{\"suite\":\"$SUITE\",\"profile\":\"$PROFILE\",\"unit_serial\":\"SMOKE\"}" \
    -o "$START" -w '%{http_code}')
[ "$CODE" = 201 ] || fail "starting $SUITE with $PROFILE answered $CODE: $(cat "$START")"
RUN=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['run_id'])" "$START")
echo "==> started $RUN"

STATUS=
for _ in $(seq 1 90); do
    STATUS=$(curl -fsS "$BASE/api/runs/$RUN" | python3 -c "import json,sys; print(json.load(sys.stdin)['status'])")
    case $STATUS in running | starting | stopping) sleep 1 ;; *) break ;; esac
done
[ "$STATUS" = "passed" ] || fail "$SUITE finished $STATUS, expected passed"
echo "==> $SUITE passed"

# Quitting has to take the backend's process group with it, or a suite mid-run
# outlives the app that started it.
#
# Only the main process is signalled, found as the backend's parent. Electron's
# helpers share its executable path, so signalling by that path would take the
# GPU process with it and the main process would abort rather than quit, which
# is the one path where its quit handler does not run.
#
# The interpreter's flags are not spelled out, so adding one does not turn
# this into "no backend process" and read as a teardown that failed.
BACKEND=$(pgrep -f "resources/runtime/bin/python3 .*-m gauntlet" | head -1 || true)
[ -n "$BACKEND" ] || fail "no backend process to check the teardown against"
MAIN=$(ps -o ppid= -p "$BACKEND" | tr -d ' ')
kill -TERM "$MAIN"
# Electron must relay shutdown through its main process before the packaged
# backend exits. Under Jenkins' persistent `docker exec` container, the
# terminated detached backend can remain a zombie until PID 1 reaps it. A
# zombie has no running backend even though `kill -0` still succeeds, so poll
# for a non-zombie process rather than treating that container-reaping detail
# as a product failure.
backend_running() {
    kill -0 "$BACKEND" 2>/dev/null || return 1
    case $(ps -o stat= -p "$BACKEND" 2>/dev/null | tr -d ' ') in
        Z* | "") return 1 ;;
        *) return 0 ;;
    esac
}
for _ in $(seq 1 30); do
    backend_running || break
    sleep 1
done
if backend_running; then
    fail "the backend outlived the app"
fi
echo "==> the backend went with the app"
echo "smoke: ok"
