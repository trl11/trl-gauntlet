#!/usr/bin/env bash
#
# A conforming suite with no Gauntlet dependency.
#
# The whole contract, in bash: read GAUNTLET_RUN_DIR, append one JSON object
# per iteration to metrics.jsonl, and write verdict.json before exiting.
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

# Profiles are the suite's own business. Gauntlet never parses them, so a
# couple of greps is a legitimate way to read one.
read_profile_key() {
    local key="$1" fallback="$2"
    [[ -f "$profile" ]] || { echo "$fallback"; return; }
    local value
    value="$(grep -E "^${key}:" "$profile" | head -1 | sed -E "s/^${key}:[[:space:]]*//" | tr -d '"')"
    echo "${value:-$fallback}"
}

iterations="${iterations:-$(read_profile_key iterations 5)}"
period_s="$(read_profile_key period_s 0.2)"
fail_at="$(read_profile_key fail_at 0)"

metrics="$run_dir/metrics.jsonl"
: > "$metrics"

echo "example_shell: ${iterations} iterations, target=${target:-none}"

started="$(date +%s)"
failures=0

for (( i = 1; i <= iterations; i++ )); do
    value=$(( RANDOM % 100 ))
    if [[ "$fail_at" -gt 0 && "$i" -eq "$fail_at" ]]; then
        success=false
        reason="injected failure at iteration ${i}"
        failures=$(( failures + 1 ))
        echo "error: iter ${i}: FAIL ${reason}"
    else
        success=true
        reason=""
        echo "iter ${i}: ok  value=${value}"
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
