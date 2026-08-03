#!/usr/bin/env bash
#
# __SUITE_TITLE__
#
# Implements the Gauntlet contract directly: read GAUNTLET_RUN_DIR, append one
# JSON object per iteration to metrics.jsonl, write verdict.json before exit.
#
set -euo pipefail

run_dir="${GAUNTLET_RUN_DIR:-}"
profile=""
target=""
iterations=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --run-dir)    run_dir="$2"; shift 2 ;;
        --profile)    profile="$2"; shift 2 ;;
        --target)     target="$2"; shift 2 ;;
        --iterations) iterations="$2"; shift 2 ;;
        *)            echo "warn: ignoring unknown argument $1"; shift ;;
    esac
done

if [[ -z "$run_dir" ]]; then
    echo "error: no run directory (pass --run-dir or set GAUNTLET_RUN_DIR)" >&2
    exit 2
fi
mkdir -p "$run_dir"

# Gauntlet does not parse profiles; each suite reads its own.
read_profile_key() {
    local key="$1" fallback="$2" value
    [[ -f "$profile" ]] || { echo "$fallback"; return; }
    value="$(grep -E "^${key}:" "$profile" | head -1 | sed -E "s/^${key}:[[:space:]]*//" | tr -d '"')"
    echo "${value:-$fallback}"
}

iterations="${iterations:-$(read_profile_key iterations 5)}"
period_s="$(read_profile_key period_s 1.0)"

metrics="$run_dir/metrics.jsonl"
: > "$metrics"

echo "__SUITE_KEY__: ${iterations} iterations, target=${target:-none}"

started="$(date +%s)"
failures=0

for (( i = 1; i <= iterations; i++ )); do
    # TODO: replace with the real measurement. Set success=false and a reason
    # to fail an iteration.
    value=1
    success=true
    reason=""

    if [[ "$success" == true ]]; then
        echo "iter ${i}: ok  value=${value}"
    else
        echo "error: iter ${i}: FAIL ${reason}"
        failures=$(( failures + 1 ))
    fi

    now="$(date +%s)"
    printf '{"kind":"iteration","iteration":%d,"timestamp":%d,"elapsed_run_s":%d,"success":%s,"reason":"%s","metrics":{"value":%d}}\n' \
        "$i" "$now" "$(( now - started ))" "$success" "$reason" "$value" >> "$metrics"

    sleep "$period_s"
done

ended="$(date +%s)"
if [[ "$failures" -eq 0 ]]; then
    passed=true
    reason=""
else
    passed=false
    reason="${failures}/${iterations} iterations failed"
fi

cat > "$run_dir/verdict.json" <<EOF
{
  "passed": ${passed},
  "reason": "${reason}",
  "total_iterations": ${iterations},
  "successes": $(( iterations - failures )),
  "failures": ${failures},
  "duration_s": $(( ended - started )),
  "aborted": false
}
EOF

echo "done: $( [[ "$passed" == true ]] && echo PASS || echo FAIL )"
[[ "$passed" == true ]] && exit 0 || exit 1
