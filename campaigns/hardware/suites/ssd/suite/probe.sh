#!/usr/bin/env bash
#
# Per-tick SSD probe.
#
# Runs on the unit under test over a single SSH round-trip.
# Probes one device per invocation — the runner calls this once per
# configured device per tick.
#
#   1. `dd` write of TEST_SIZE_MB to TEST_PATH → write MB/s.
#   2. Mandatory cache drop, then `dd` read of TEST_PATH → read MB/s.
#   3. Write-verify: pipe /dev/urandom into a tee that lands both in
#      TEST_PATH.verify and a sha256 sink (the "what we wrote" hash),
#      drop caches, sha256 the file on disk → second hash. Mismatch
#      means the storage corrupted the data.
#   4. `smartctl -A -j DEVICE` → NVMe media/ECC counters. Returns an
#      empty object if smartctl isn't installed (e.g. on eMMC-only
#      units) or sudo is denied; the runner doesn't fail the tick on
#      that.
#
# Cache drop and smartctl both need root. The script uses `sudo -n`;
# a sudo failure on the cache drop is treated as a hard error
# (read MB/s would otherwise be cache-served and meaningless) and
# returned via the "cache_drop_failed" field so the runner can record
# a discrete anomaly.
#
# Emits a single JSON line on stdout on success.
#
# Positional args:
#   $1 DEVICE         /dev/nvmeXn1 / /dev/mmcblk0 ...  (SMART target only)
#   $2 TEST_PATH      absolute path to the bandwidth/verify file
#   $3 TEST_SIZE_MB   MiB written/read each tick
#   $4 VERIFY_SIZE_KB KiB written/reread for write-verify

set -u

DEVICE="$1"
TEST_PATH="$2"
TEST_SIZE_MB="$3"
VERIFY_SIZE_KB="$4"

err=""
write_mbps=null
read_mbps=null
verify_ok=null
verify_expected_sha=null
verify_actual_sha=null
cache_drop_failed=false
smart_json="{}"

mkdir -p "$(dirname "$TEST_PATH")" 2>/dev/null || true

# Parse a single "MB/s" or "GB/s" rate out of dd's stderr summary line.
# dd prints something like:
#   "67108864 bytes (67 MB, 64 MiB) copied, 0.026 s, 2.5 GB/s"
parse_rate() {
  awk '
    /bytes/ {
      n = split($0, parts, ",")
      for (i = 1; i <= n; i++) {
        if (parts[i] ~ /MB\/s/ || parts[i] ~ /GB\/s/) {
          gsub(/^[ \t]+|[ \t]+$/, "", parts[i])
          split(parts[i], rate, " ")
          val = rate[1]
          if (parts[i] ~ /GB\/s/) val = val * 1024
          printf "%s\n", val
          exit
        }
      }
    }'
}

# Cache drop is mandatory for the read measurement to be meaningful.
# Returns 0 on success, 1 if sudo is denied. Stderr quiet either way.
drop_caches() {
  sync
  sudo -n sh -c 'echo 3 > /proc/sys/vm/drop_caches' 2>/dev/null
}

# --- dd write ---
dd_write_err=$(dd if=/dev/urandom of="$TEST_PATH" bs=1M count="$TEST_SIZE_MB" conv=fdatasync 2>&1 >/dev/null) \
  || err="${err}write_failed;"
write_mbps=$(printf '%s\n' "$dd_write_err" | parse_rate)
[ -z "$write_mbps" ] && write_mbps=null

# --- Cache drop before the read measurement ---
if ! drop_caches; then
  cache_drop_failed=true
fi

# --- dd read ---
dd_read_err=$(dd if="$TEST_PATH" of=/dev/null bs=1M count="$TEST_SIZE_MB" 2>&1 >/dev/null) \
  || err="${err}read_failed;"
read_mbps=$(printf '%s\n' "$dd_read_err" | parse_rate)
[ -z "$read_mbps" ] && read_mbps=null

# --- Write-verify ---
# Pattern comes from /dev/urandom *on the UUT*: tee it into the file
# and a sha256 sink at the same time, then drop caches and rehash the
# on-disk file. No bytes traverse SSH so we sidestep ARG_MAX entirely.
VERIFY_PATH="${TEST_PATH}.verify"
verify_size_bytes=$((VERIFY_SIZE_KB * 1024))
verify_expected_sha=$(head -c "$verify_size_bytes" /dev/urandom \
  | tee "$VERIFY_PATH" \
  | sha256sum \
  | awk '{print $1}')
sync
drop_caches >/dev/null 2>&1 || true
verify_actual_sha=$(sha256sum "$VERIFY_PATH" 2>/dev/null | awk '{print $1}')
if [ -n "$verify_expected_sha" ] && [ "$verify_expected_sha" = "$verify_actual_sha" ]; then
  verify_ok=true
else
  verify_ok=false
fi
verify_expected_sha="\"$verify_expected_sha\""
verify_actual_sha="\"$verify_actual_sha\""
rm -f "$VERIFY_PATH" 2>/dev/null || true

# --- SMART (NVMe) — needs sudo; degrade gracefully if absent ---
if command -v smartctl >/dev/null 2>&1; then
  smart_raw=$(sudo -n smartctl -A -j "$DEVICE" 2>/dev/null || true)
  if [ -n "$smart_raw" ]; then
    smart_json=$(printf '%s' "$smart_raw" | python3 -c '
import json, sys
try:
    raw = json.loads(sys.stdin.read() or "{}")
except Exception as exc:
    print(json.dumps({"parse_error": str(exc)}))
    sys.exit(0)
log = raw.get("nvme_smart_health_information_log") or {}
keep = (
    "media_errors",
    "num_err_log_entries",
    "critical_warning",
    "percentage_used",
    "available_spare",
    "available_spare_threshold",
    "unsafe_shutdowns",
    "temperature",
)
print(json.dumps({k: log.get(k) for k in keep if k in log}))
')
  fi
fi

[ -z "$err" ] && err_json=null || err_json="\"$err\""

printf '%s\n' "{\"write_mbps\": $write_mbps, \"read_mbps\": $read_mbps, \"verify_ok\": $verify_ok, \"verify_expected_sha\": $verify_expected_sha, \"verify_actual_sha\": $verify_actual_sha, \"cache_drop_failed\": $cache_drop_failed, \"smart\": $smart_json, \"error\": $err_json}"
