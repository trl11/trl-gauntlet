"""Runs the SSD probe and evaluates its results.

The measurement is ``probe.sh``, fed to the unit on stdin. One SSH round-trip
per device per tick returns write bandwidth, read bandwidth, a SHA-256
write-verify, and SMART counters.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_sdk.remote import RemoteError, run, shell_quote

from suite.profile import Device, ProbeBlock

PROBE_SCRIPT = Path(__file__).parent / "probe.sh"

# SMART counters watched, and the anomaly kind each increase raises.
SMART_COUNTERS = {
    "media_errors": "media_error_increased",
    "num_err_log_entries": "error_log_increased",
    "critical_warning": "critical_warning_raised",
}


@dataclass
class Anomaly:
    """One detected anomaly, recorded by the caller."""

    probe: str
    kind: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class DeviceState:
    """Rolling accumulators for one device across a session."""

    smart_baseline: dict[str, int] = field(default_factory=dict)
    last_smart: dict[str, Any] = field(default_factory=dict)
    write_samples: list[float] = field(default_factory=list)
    read_samples: list[float] = field(default_factory=list)
    verify_attempts: int = 0
    verify_failures: int = 0

    def mean_write(self) -> float:
        return sum(self.write_samples) / len(self.write_samples) if self.write_samples else 0.0

    def mean_read(self) -> float:
        return sum(self.read_samples) / len(self.read_samples) if self.read_samples else 0.0


def load_script() -> str:
    return PROBE_SCRIPT.read_text()


def read_smart_baseline(client: Any, devices: list[Device], *, timeout: float) -> dict[str, dict[str, int]]:
    """Snapshot SMART counters before the session starts.

    Anomalies are increases relative to this baseline.
    """
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
            counters = {k: int(v) for k, v in log.items() if k in SMART_COUNTERS and isinstance(v, (int, float))}
        baselines[device.name] = counters
    return baselines


def probe_command(block: ProbeBlock, device: Device) -> str:
    """Build the per-tick command for one device.

    The script is fed in on stdin rather than installed, so a unit keeps
    nothing of the suite between runs.
    """
    args = f"{shell_quote(device.device)} {shell_quote(device.test_path)} {int(block.test_size_mb)} {int(block.verify_size_kb)}"
    return f"bash -s -- {args} <<'GAUNTLET_PROBE_EOF'\n{load_script()}\nGAUNTLET_PROBE_EOF"


def run_probe(client: Any, block: ProbeBlock, device: Device) -> dict[str, Any]:
    """Probe one device and return its parsed JSON result.

    Raises on transport failure or unparsable output.
    """
    result = run(client, probe_command(block, device), timeout=block.ssh_timeout_s)
    text = result.stdout.strip()
    if not text:
        raise RemoteError(f"probe produced no output for {device.name}: {result.stderr.strip()[-300:]}")
    try:
        # The script prints its JSON as the last line; earlier lines are
        # login-shell output.
        return dict(json.loads(text.splitlines()[-1]))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RemoteError(f"probe output for {device.name} was not JSON: {exc}") from exc


def synth_probe(device: Device) -> dict[str, Any]:
    """Synthetic results for the mock driver, varied per device."""
    salt = sum(ord(c) for c in device.name) % 200
    return {
        "write_mbps": 2400.0 - salt,
        "read_mbps": 3100.0 - salt,
        "verify_ok": True,
        "verify_expected_sha": "0" * 64,
        "verify_actual_sha": "0" * 64,
        "cache_drop_failed": False,
        "smart": {"media_errors": 0, "num_err_log_entries": 0, "critical_warning": 0},
        "error": None,
    }


def evaluate(state: DeviceState, block: ProbeBlock, device: Device, result: dict[str, Any]) -> list[Anomaly]:
    """Fold one probe result into ``state`` and return what was anomalous."""
    anomalies: list[Anomaly] = []

    if result.get("test_path_ok") is False:
        # No bandwidth was measured: the figure would have been another disk's.
        anomalies.append(
            Anomaly(
                "device",
                "test_path_not_on_device",
                {
                    "device": device.name,
                    "device_disk": result.get("device_disk"),
                    "test_path": device.test_path,
                    "test_path_disk": result.get("test_path_disk"),
                },
            )
        )

    if result.get("cache_drop_failed"):
        # Reads are served from page cache when the drop fails.
        anomalies.append(Anomaly("ssh", "cache_drop_failed", {"device": device.name}))

    for key, samples, floor, kind in (
        ("write_mbps", state.write_samples, block.write_floor_mbps, "write_below_floor"),
        ("read_mbps", state.read_samples, block.read_floor_mbps, "read_below_floor"),
    ):
        value = result.get(key)
        if not isinstance(value, (int, float)):
            continue
        samples.append(float(value))
        if floor is not None and value < floor:
            anomalies.append(
                Anomaly("bandwidth", kind, {"device": device.name, "observed_mbps": value, "floor_mbps": floor})
            )

    verify_ok = result.get("verify_ok")
    if verify_ok is not None:
        state.verify_attempts += 1
        if verify_ok is False:
            state.verify_failures += 1
            anomalies.append(
                Anomaly(
                    "write_verify",
                    "miscompare",
                    {
                        "device": device.name,
                        "expected_sha": result.get("verify_expected_sha"),
                        "actual_sha": result.get("verify_actual_sha"),
                    },
                )
            )

    smart = result.get("smart")
    state.last_smart = dict(smart) if isinstance(smart, dict) else {}
    for key, kind in SMART_COUNTERS.items():
        current = state.last_smart.get(key)
        if not isinstance(current, (int, float)):
            continue
        baseline = state.smart_baseline.get(key, 0)
        if current > baseline:
            anomalies.append(
                Anomaly(
                    "smart",
                    kind,
                    {"device": device.name, "counter": key, "baseline": baseline, "current": int(current)},
                )
            )
            # Re-baseline so only further increases raise again.
            state.smart_baseline[key] = int(current)
    return anomalies
