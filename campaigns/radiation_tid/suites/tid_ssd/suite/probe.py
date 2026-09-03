"""Runs the SSD probe and judges what came back.

The measurement is ``probe.sh``, executed on the unit. One SSH round-trip per
device per tick returns whether the device is still on the bus, write and read
bandwidth, a SHA-256 write-verify, and the NVMe health log.

Nothing here decides a run. Every judgement is an :class:`Anomaly` carrying the
numbers it compared and the sentence the run log needs, and the runner records
it and carries on: under dose the part is expected to degrade, and a tick that
found something wrong is a measurement, not a reason to stop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_sdk.remote import RemoteError, run, shell_quote

from suite.profile import Device, DmesgBlock, ProbeBlock, ProvisionBlock

PROBE_SCRIPT = Path(__file__).parent / "probe.sh"

# Health-log fields that only ever climb. Each increase over the baseline is an
# anomaly of its own kind, and re-baselines so only a further step raises again.
RISING_COUNTERS = {
    "critical_warning": "critical_warning_raised",
    "media_errors": "media_error_increased",
    "num_err_log_entries": "error_log_increased",
    "percentage_used": "wear_increased",
    "unsafe_shutdowns": "unsafe_shutdown",
}

# Health-log fields read as levels rather than counts. Recorded every tick;
# `available_spare` is the one that is also judged.
GAUGES = ("available_spare", "available_spare_threshold", "temperature")


@dataclass
class Anomaly:
    """One detected anomaly, recorded and announced by the caller."""

    probe: str
    kind: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceState:
    """Rolling accumulators for one device across a session."""

    smart_baseline: dict[str, int] = field(default_factory=dict)
    last_smart: dict[str, Any] = field(default_factory=dict)
    spare_baseline: int | None = None
    write_samples: list[float] = field(default_factory=list)
    read_samples: list[float] = field(default_factory=list)
    verify_attempts: int = 0
    verify_failures: int = 0
    missing_ticks: int = 0
    spare_under_threshold: bool = False

    def mean_write(self) -> float:
        return sum(self.write_samples) / len(self.write_samples) if self.write_samples else 0.0

    def mean_read(self) -> float:
        return sum(self.read_samples) / len(self.read_samples) if self.read_samples else 0.0


def load_script() -> str:
    return PROBE_SCRIPT.read_text()


def install(client: Any, install_dir: str, *, timeout: float) -> str:
    """Copy the probe onto the unit and return its remote path."""
    remote_path = f"{install_dir.rstrip('/')}/probe.sh"
    body = shell_quote(load_script())
    command = f"mkdir -p {shell_quote(install_dir)} && printf '%s' {body} > {shell_quote(remote_path)} && chmod +x {shell_quote(remote_path)}"
    result = run(client, command, timeout=timeout)
    if not result.ok:
        raise RemoteError(f"installing probe to {remote_path}: {result.output}")
    return remote_path


def provision(client: Any, block: ProvisionBlock, *, ssh_user: str, timeout: float) -> None:
    """Make ``mount_point`` a writable filesystem on the unit.

    Returns without formatting when a filesystem is already mounted there.
    """
    mount = shell_quote(block.mount_point)
    already = run(client, f"mountpoint -q {mount} && echo mounted || true", timeout=timeout)
    if "mounted" in already.stdout:
        return

    if block.format_device:
        fmt = run(
            client,
            f"sudo -n mkfs.{block.filesystem} -F {shell_quote(block.device)}",
            timeout=block.format_timeout_s,
        )
        if not fmt.ok:
            raise RemoteError(f"formatting {block.device}: {fmt.output}")

    steps = (
        f"sudo -n mkdir -p {mount}",
        f"sudo -n mount {shell_quote(block.device)} {mount}",
        f"sudo -n chown {shell_quote(ssh_user)} {mount}",
    )
    for step in steps:
        result = run(client, step, timeout=timeout)
        if not result.ok:
            raise RemoteError(f"provisioning ({step}): {result.output}")


def read_smart_baseline(client: Any, devices: list[Device], *, timeout: float) -> dict[str, dict[str, int]]:
    """Snapshot the health log before the session starts.

    Anomalies are movement relative to this, so a disk that arrived with
    counters already on the clock is judged on what the beam added.
    """
    watched = set(RISING_COUNTERS) | set(GAUGES)
    baselines: dict[str, dict[str, int]] = {}
    for device in devices:
        counters: dict[str, int] = {}
        try:
            result = run(client, f"sudo -n smartctl -A -j {shell_quote(device.device)}", timeout=timeout)
        except (RemoteError, TimeoutError):
            baselines[device.name] = counters
            continue
        if result.ok and result.stdout.strip():
            try:
                log = json.loads(result.stdout).get("nvme_smart_health_information_log") or {}
            except json.JSONDecodeError:
                log = {}
            counters = {k: int(v) for k, v in log.items() if k in watched and isinstance(v, (int, float))}
        baselines[device.name] = counters
    return baselines


def probe_command(block: ProbeBlock, device: Device, installed_path: str | None) -> str:
    """Build the per-tick command for one device."""
    args = f"{shell_quote(device.device)} {shell_quote(device.test_path)} {int(block.test_size_mb)} {int(block.verify_size_kb)}"
    if installed_path:
        return f"bash {shell_quote(installed_path)} {args}"
    return f"bash -s -- {args} <<'GAUNTLET_PROBE_EOF'\n{load_script()}\nGAUNTLET_PROBE_EOF"


def run_probe(client: Any, block: ProbeBlock, device: Device, installed_path: str | None) -> dict[str, Any]:
    """Probe one device and return its parsed JSON result.

    Raises on transport failure or unparsable output.
    """
    result = run(client, probe_command(block, device, installed_path), timeout=block.ssh_timeout_s)
    text = result.stdout.strip()
    if not text:
        raise RemoteError(f"probe produced no output for {device.name}: {result.stderr.strip()[-300:]}")
    try:
        # The script prints its JSON as the last line; earlier lines are
        # login-shell output.
        return dict(json.loads(text.splitlines()[-1]))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RemoteError(f"probe output for {device.name} was not JSON: {exc}") from exc


def read_kernel_log(client: Any, block: DmesgBlock, *, since_cursor: str, timeout: float) -> tuple[list[str], str]:
    """Messages the kernel logged since ``since_cursor``, and the new cursor.

    Reads the whole ring and slices it at the last line already seen, because
    `dmesg` on the units this suite runs against has no cursor of its own.
    """
    result = run(client, "dmesg 2>/dev/null || sudo -n dmesg", timeout=timeout)
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    if not lines:
        return [], since_cursor
    fresh = lines
    if since_cursor:
        for index in range(len(lines) - 1, -1, -1):
            if lines[index] == since_cursor:
                fresh = lines[index + 1 :]
                break
    wanted = [p.lower() for p in block.patterns]
    return [line for line in fresh if any(p in line.lower() for p in wanted)], lines[-1]


def evaluate(state: DeviceState, block: ProbeBlock, device: Device, result: dict[str, Any]) -> list[Anomaly]:
    """Fold one probe result into ``state`` and return what was anomalous."""
    anomalies: list[Anomaly] = []

    if result.get("device_present") is False:
        state.missing_ticks += 1
        anomalies.append(
            Anomaly(
                "device",
                "missing",
                f"{device.name}: {device.device} is no longer a block device — the drive has left the bus, "
                "and the bandwidth and health readings for this tick mean nothing",
                {"device": device.name, "path": device.device},
            )
        )

    if result.get("test_path_ok") is False:
        anomalies.append(
            Anomaly(
                "device",
                "test_path_not_on_device",
                f"{device.name}: {device.test_path} is on "
                f"{result.get('test_path_disk') or 'no block device'}, not on {device.device} — "
                "no bandwidth was measured this tick, because the figure would have been another disk's",
                {
                    "device": device.name,
                    "device_disk": result.get("device_disk"),
                    "test_path": device.test_path,
                    "test_path_disk": result.get("test_path_disk"),
                },
            )
        )

    if result.get("cache_drop_failed"):
        anomalies.append(
            Anomaly(
                "ssh",
                "cache_drop_failed",
                f"{device.name}: could not drop the page cache, so this tick's read rate was served from "
                "memory and is not a measurement of the disk",
                {"device": device.name},
            )
        )

    anomalies += _bandwidth(state, block, device, result)
    anomalies += _verify(state, device, result)
    anomalies += _health(state, device, result)
    return anomalies


def _bandwidth(state: DeviceState, block: ProbeBlock, device: Device, result: dict[str, Any]) -> list[Anomaly]:
    anomalies: list[Anomaly] = []
    for key, samples, floor, kind, what in (
        ("write_mbps", state.write_samples, block.write_floor_mbps, "write_below_floor", "writes"),
        ("read_mbps", state.read_samples, block.read_floor_mbps, "read_below_floor", "reads"),
    ):
        value = result.get(key)
        if not isinstance(value, (int, float)):
            continue
        samples.append(float(value))
        if floor is not None and value < floor:
            anomalies.append(
                Anomaly(
                    "bandwidth",
                    kind,
                    f"{device.name}: {what} at {value:.0f} MB/s, below the {floor:.0f} MB/s floor — the part "
                    "is still working but slower than it was",
                    {"device": device.name, "observed_mbps": value, "floor_mbps": floor},
                )
            )
    return anomalies


def _verify(state: DeviceState, device: Device, result: dict[str, Any]) -> list[Anomaly]:
    verify_ok = result.get("verify_ok")
    if verify_ok is None:
        return []
    state.verify_attempts += 1
    if verify_ok is not False:
        return []
    state.verify_failures += 1
    return [
        Anomaly(
            "write_verify",
            "miscompare",
            f"{device.name}: a block read back with a different SHA-256 than it was written with — the disk "
            "returned corrupt data, which is the failure this suite exists to catch",
            {
                "device": device.name,
                "expected_sha": result.get("verify_expected_sha"),
                "actual_sha": result.get("verify_actual_sha"),
            },
        )
    ]


def _health(state: DeviceState, device: Device, result: dict[str, Any]) -> list[Anomaly]:
    """Judge the NVMe health log against the session baseline."""
    smart = result.get("smart")
    state.last_smart = dict(smart) if isinstance(smart, dict) else {}
    anomalies: list[Anomaly] = []

    for key, kind in RISING_COUNTERS.items():
        current = state.last_smart.get(key)
        if not isinstance(current, (int, float)):
            continue
        baseline = state.smart_baseline.get(key, 0)
        if current > baseline:
            anomalies.append(
                Anomaly(
                    "smart",
                    kind,
                    f"{device.name}: NVMe {key} rose from {baseline} to {int(current)}",
                    {"device": device.name, "counter": key, "baseline": baseline, "current": int(current)},
                )
            )
            # Re-baseline so only further increases raise again.
            state.smart_baseline[key] = int(current)

    spare = state.last_smart.get("available_spare")
    if isinstance(spare, (int, float)):
        spare = int(spare)
        if state.spare_baseline is None:
            state.spare_baseline = spare
        threshold = state.last_smart.get("available_spare_threshold")
        if spare < state.spare_baseline:
            anomalies.append(
                Anomaly(
                    "smart",
                    "spare_depleting",
                    f"{device.name}: available spare fell from {state.spare_baseline}% to {spare}% — the "
                    "controller is retiring blocks",
                    {"device": device.name, "baseline_pct": state.spare_baseline, "current_pct": spare},
                )
            )
            state.spare_baseline = spare
        if isinstance(threshold, (int, float)) and spare < threshold and not state.spare_under_threshold:
            # Latched: once the drive is under its own threshold it stays
            # there, and one anomaly says so better than one every tick.
            state.spare_under_threshold = True
            anomalies.append(
                Anomaly(
                    "smart",
                    "spare_below_threshold",
                    f"{device.name}: available spare {spare}% is under the drive's own {int(threshold)}% "
                    "threshold — the controller considers itself out of spare capacity",
                    {"device": device.name, "current_pct": spare, "threshold_pct": int(threshold)},
                )
            )
    return anomalies
