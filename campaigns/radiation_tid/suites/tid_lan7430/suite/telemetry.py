"""Turns one collector sample into metrics and anomalies.

The collector reports cumulative counters, so what matters is the difference
against the previous tick: a part that logged ten thousand CRC errors before
the beam started is not failing now, and a part that logged three since the
last tick is. The first sample of a session establishes the baseline and
raises nothing.

The OTP and register images work the other way round: the image read at setup
is the reference for the whole session, because a bit that flips and stays
flipped must keep reporting for as long as it stays wrong.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from gauntlet_sdk import AnomalyLog
from gauntlet_sdk.remote import RemoteError, run, shell_quote

from suite.anomaly import flag
from suite.profile import TidLan7430Profile

BYTE_COUNTER_MODULUS = 2**32

COLLECTOR_NAME = "lan7430_collector.py"

# Counters whose every increment is worth an anomaly. The rest are recorded
# but not judged, because a dropped packet on a busy link is ordinary.
JUDGED_COUNTERS = (
    "rx_crc_errors",
    "rx_errors",
    "rx_fifo_errors",
    "rx_frame_errors",
    "rx_length_errors",
    "rx_missed_errors",
    "rx_over_errors",
    "tx_aborted_errors",
    "tx_carrier_errors",
    "tx_errors",
    "tx_fifo_errors",
    "tx_window_errors",
)


@dataclass
class TelemetryState:
    """What the previous ticks established, held for the length of the run."""

    dmesg_cursor: float = 0.0
    golden_mac: str = ""
    golden_otp_sha: str = ""
    golden_registers_sha: str = ""
    mac: str = ""
    mac_changes: int = 0
    otp_changes: int = 0
    otp_sha: str = ""
    previous_aer: dict[str, int] = field(default_factory=dict)
    previous_ethtool: dict[str, int] = field(default_factory=dict)
    previous_statistics: dict[str, int] = field(default_factory=dict)
    register_changes: int = 0
    seen_first_sample: bool = False


def install_collector(client: Any, install_dir: str, source: str, *, timeout: float) -> str:
    """Copy the collector onto the unit and return its remote path."""
    remote_path = f"{install_dir.rstrip('/')}/{COLLECTOR_NAME}"
    command = f"mkdir -p {shell_quote(install_dir)} && printf '%s' {shell_quote(source)} > {shell_quote(remote_path)}"
    result = run(client, command, timeout=timeout)
    if not result.ok:
        raise RemoteError(f"installing collector to {remote_path}: {result.output}")
    return remote_path


def collector_config(profile: TidLan7430Profile, state: TelemetryState) -> dict[str, Any]:
    """The JSON argument the collector is invoked with this tick."""
    return {
        "dmesg_cursor": state.dmesg_cursor,
        "dmesg_enabled": profile.dmesg.enabled,
        "dmesg_max_lines": profile.dmesg.max_lines,
        "dmesg_patterns": profile.dmesg.patterns,
        "interface": profile.interface.name,
        "otp_enabled": profile.otp.enabled,
        "otp_length": profile.otp.length,
        "pci_slot": profile.interface.pci_slot,
        "registers_enabled": profile.otp.enabled and profile.otp.check_registers,
    }


def collect(client: Any, remote_path: str, config: dict[str, Any], *, timeout: float) -> dict[str, Any]:
    """Run the collector once and return the sample it printed."""
    command = f"python3 {shell_quote(remote_path)} {shell_quote(json.dumps(config))}"
    result = run(client, command, timeout=timeout)
    text = result.stdout.strip()
    if not text:
        raise RemoteError(f"collector produced no output: {result.stderr.strip()[-300:]}")
    try:
        return dict(json.loads(text.splitlines()[-1]))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RemoteError(f"collector output was not JSON: {exc}") from exc


def _deltas(previous: dict[str, int], current: dict[str, int]) -> dict[str, int]:
    """Per-counter increase since the previous tick.

    A counter that went backwards has been reset, by a driver reload or by the
    device falling off the bus and coming back. That is reported as zero
    rather than a negative, and the reset itself is visible in the kernel log.
    """
    changed = {}
    for name, value in current.items():
        before = previous.get(name)
        if before is None:
            continue
        step = value - before
        if step > 0:
            changed[name] = step
    return changed


def _byte_delta(previous: dict[str, int], current: dict[str, int], name: str) -> int:
    """Increase in one byte counter, reading a backwards step as a 32-bit wrap.

    This part's byte counters are 32 bits wide, so at gigabit they wrap about
    every thirty-six seconds — more than once between ticks at any useful
    sample period. `_deltas` reads a wrap as a reset and drops the counter,
    which takes the receive half out of the tick's total and leaves the traffic
    cross-check reading as though the traffic had gone around the part.
    """
    before = previous.get(name)
    after = current.get(name)
    if before is None or after is None:
        return 0
    if after >= before:
        return after - before
    return after + BYTE_COUNTER_MODULUS - before


def _analyse_link(
    sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    iteration: int,
    anomalies: AnomalyLog,
) -> dict[str, Any]:
    """Link state, and what changed about it."""
    link = dict(sample.get("link") or {})
    operstate = str(link.get("operstate") or "")
    speed = link.get("speed_mbps")

    if operstate != "up":
        flag(
            anomalies,
            "link",
            "down",
            iteration=iteration,
            message=(
                f"{profile.interface.name} is down (operstate {operstate}): the part is no longer "
                "presenting a link, and this tick measured nothing"
            ),
            detail={"operstate": operstate},
        )
    elif isinstance(speed, (int, float)) and speed < profile.interface.expected_speed_mbps:
        flag(
            anomalies,
            "link",
            "speed_degraded",
            iteration=iteration,
            message=(
                f"{profile.interface.name} negotiated {speed} Mbps where "
                f"{profile.interface.expected_speed_mbps} Mbps was expected: the link came up degraded, "
                "so every throughput number this tick is capped by the link and not by the part"
            ),
            detail={"expected_mbps": profile.interface.expected_speed_mbps, "observed_mbps": speed},
        )

    address = str(link.get("address") or "")
    if address and not state.golden_mac:
        state.golden_mac = address
    elif address and address != state.golden_mac:
        # The controller loads its MAC from the OTP into its address registers
        # at reset and the driver reads it from there, so a changed one means
        # the image behind it moved and the part has reloaded since.
        state.mac_changes += 1
        flag(
            anomalies,
            "link",
            "mac_changed",
            iteration=iteration,
            message=(
                f"the MAC on {profile.interface.name} changed from {state.golden_mac} to {address}: "
                "the part is reading a different address out of its OTP than it did at setup"
            ),
            detail={"baseline": state.golden_mac, "observed": address},
        )
    if address:
        state.mac = address
    return link


def _analyse_counters(
    sample: dict[str, Any],
    state: TelemetryState,
    iteration: int,
    anomalies: AnomalyLog,
) -> dict[str, Any]:
    """Error counters, as the increase since the previous tick."""
    statistics = {k: int(v) for k, v in (sample.get("statistics") or {}).items() if isinstance(v, int)}
    ethtool = {k: int(v) for k, v in (sample.get("ethtool_stats") or {}).items() if isinstance(v, int)}

    previous_statistics = state.previous_statistics
    steps = _deltas(previous_statistics, statistics)
    ethtool_steps = _deltas(state.previous_ethtool, ethtool)
    state.previous_statistics = statistics
    state.previous_ethtool = ethtool

    for name in JUDGED_COUNTERS:
        step = steps.get(name)
        if step:
            flag(
                anomalies,
                "counters",
                name,
                iteration=iteration,
                message=(
                    f"{name} rose by {step} this tick, {statistics.get(name)} in the session: the part is "
                    "making errors it was not making before"
                ),
                detail={"since_last_tick": step, "total": statistics.get(name)},
            )

    recorded = {name: steps.get(name, 0) for name in JUDGED_COUNTERS if name in statistics}
    recorded["rx_dropped"] = steps.get("rx_dropped", 0)
    recorded["tx_dropped"] = steps.get("tx_dropped", 0)
    # What the interface itself carried since the previous tick, which is how
    # the run checks the traffic went over the part rather than around it.
    recorded["bytes_step"] = _byte_delta(previous_statistics, statistics, "rx_bytes") + _byte_delta(
        previous_statistics, statistics, "tx_bytes"
    )
    recorded["ethtool_error_steps"] = sum(
        step for name, step in ethtool_steps.items() if "err" in name.lower() or "drop" in name.lower()
    )
    return recorded


def _analyse_pcie(
    sample: dict[str, Any],
    state: TelemetryState,
    iteration: int,
    anomalies: AnomalyLog,
) -> dict[str, Any]:
    """PCIe link state and the AER counters underneath it."""
    pcie = dict(sample.get("pcie") or {})
    if not pcie:
        return {}
    if pcie.get("present") is False:
        flag(
            anomalies,
            "pcie",
            "device_missing",
            iteration=iteration,
            message=(
                f"the controller is gone from PCIe slot {pcie.get('slot')}: the part has dropped off the "
                "bus entirely, so nothing else this tick could be read"
            ),
            detail={"slot": pcie.get("slot")},
        )
        return {"present": 0}

    aer = {k: int(v) for k, v in (pcie.get("aer") or {}).items() if isinstance(v, int)}
    steps = _deltas(state.previous_aer, aer)
    state.previous_aer = aer
    for name, step in sorted(steps.items()):
        severity = name.split(".", 1)[0]
        flag(
            anomalies,
            "pcie",
            f"aer_{severity}",
            iteration=iteration,
            message=(
                f"PCIe logged {step} more {name} this tick, {aer.get(name)} in the session: the bus link to "
                "the part is taking errors"
            ),
            detail={"counter": name, "since_last_tick": step, "total": aer.get(name)},
        )

    width = pcie.get("current_link_width")
    max_width = pcie.get("max_link_width")
    if isinstance(width, int) and isinstance(max_width, int) and 0 < width < max_width:
        flag(
            anomalies,
            "pcie",
            "link_degraded",
            iteration=iteration,
            message=(
                f"PCIe renegotiated to x{width} from x{max_width}: the part has narrowed its own bus link, "
                "which caps throughput before the ethernet side is reached"
            ),
            detail={"max_width": max_width, "width": width},
        )

    return {
        "aer_steps_total": sum(steps.values()),
        "current_link_width": width,
        "present": 1,
    }


def _analyse_images(
    sample: dict[str, Any],
    state: TelemetryState,
    iteration: int,
    anomalies: AnomalyLog,
) -> dict[str, Any]:
    """The OTP and register images, compared against the session baseline."""
    metrics: dict[str, Any] = {}

    otp = dict(sample.get("otp") or {})
    if otp and not otp.get("error"):
        digest = str(otp.get("sha256") or "")
        state.otp_sha = digest
        if not state.golden_otp_sha:
            state.golden_otp_sha = digest
        elif digest and digest != state.golden_otp_sha:
            state.otp_changes += 1
            flag(
                anomalies,
                "otp",
                "changed",
                iteration=iteration,
                message=(
                    "the OTP image no longer matches the one read at setup: a bit in the part's stored "
                    "configuration has flipped"
                ),
                detail={"baseline_sha256": state.golden_otp_sha, "observed_sha256": digest},
            )
        metrics["otp_matches_baseline"] = int(digest == state.golden_otp_sha)
    elif otp.get("error"):
        flag(
            anomalies,
            "otp",
            "unreadable",
            iteration=iteration,
            message=(f"the OTP could not be read this tick, so a change in it would go unseen: {otp['error']}"),
            detail={"error": str(otp["error"])[:300]},
        )
        metrics["otp_matches_baseline"] = 0

    registers = dict(sample.get("registers") or {})
    if registers and not registers.get("error"):
        digest = str(registers.get("sha256") or "")
        if not state.golden_registers_sha:
            state.golden_registers_sha = digest
        elif digest and digest != state.golden_registers_sha:
            state.register_changes += 1
            flag(
                anomalies,
                "registers",
                "changed",
                iteration=iteration,
                message=(
                    "the register dump no longer matches the one read at setup: the part's live "
                    "configuration has moved under it"
                ),
                detail={"baseline_sha256": state.golden_registers_sha, "observed_sha256": digest},
            )
        metrics["registers_match_baseline"] = int(digest == state.golden_registers_sha)

    return metrics


def _analyse_kernel(sample: dict[str, Any], state: TelemetryState, iteration: int, anomalies: AnomalyLog) -> int:
    """Kernel messages the controller produced since the previous tick."""
    dmesg = dict(sample.get("dmesg") or {})
    if not dmesg:
        return 0
    cursor = dmesg.get("cursor")
    if isinstance(cursor, (int, float)):
        state.dmesg_cursor = float(cursor)
    lines = list(dmesg.get("lines") or [])
    for line in lines:
        flag(
            anomalies,
            "kernel",
            "message",
            iteration=iteration,
            message=f"the driver logged a new kernel message: {str(line.get('text'))[:200]}",
            detail={"at_s": line.get("at_s"), "text": str(line.get("text"))[:400]},
        )
    return len(lines)


def analyse(
    sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    iteration: int,
    anomalies: AnomalyLog,
) -> dict[str, Any]:
    """Fold one sample into metrics, recording what it says went wrong.

    The first sample only establishes baselines, so a counter that was already
    high before the session started does not read as damage done during it.
    """
    if sample.get("present") is False:
        flag(
            anomalies,
            "link",
            "interface_missing",
            iteration=iteration,
            message=(
                f"{sample.get('interface')} is not on the unit at all: the driver has lost the part, so "
                "this tick has no telemetry"
            ),
            detail={"interface": sample.get("interface")},
        )
        return {"present": 0}

    for probe, error in (sample.get("errors") or {}).items():
        # The OTP reports its own failure below, with the consequence for the
        # baseline comparison attached. Recording it here as well would count
        # one unreadable image twice against the anomaly budget.
        if probe == "otp":
            continue
        flag(
            anomalies,
            "collector",
            str(probe),
            iteration=iteration,
            message=f"the {probe} probe failed on the unit, so this tick is missing it: {str(error)[:200]}",
            detail={"error": str(error)[:300]},
        )

    link = _analyse_link(sample, state, profile, iteration, anomalies)
    counters = _analyse_counters(sample, state, iteration, anomalies)
    pcie = _analyse_pcie(sample, state, iteration, anomalies)
    images = _analyse_images(sample, state, iteration, anomalies)
    kernel_lines = _analyse_kernel(sample, state, iteration, anomalies)

    metrics: dict[str, Any] = {
        "present": 1,
        "kernel_lines": kernel_lines,
        "link": {
            "address": link.get("address"),
            "carrier_changes": link.get("carrier_changes"),
            "mtu": link.get("mtu"),
            "speed_mbps": link.get("speed_mbps"),
            "up": 1 if link.get("operstate") == "up" else 0,
        },
        "counters": counters,
    }
    if pcie:
        metrics["pcie"] = pcie
    metrics.update(images)
    temperatures = {k: v for k, v in (sample.get("temperature_c") or {}).items() if isinstance(v, (int, float))}
    if temperatures:
        metrics["temperature_c"] = temperatures
    return metrics


def establish_baseline(sample: dict[str, Any], state: TelemetryState) -> None:
    """Take the reference the session is measured against, recording nothing.

    Called once at setup with the part in its pre-exposure state. Without it
    the first tick would report the whole kernel log and every counter the
    host accumulated before Gauntlet was ever started.
    """
    state.seen_first_sample = True
    state.golden_mac = str((sample.get("link") or {}).get("address") or "")
    state.mac = state.golden_mac
    state.previous_statistics = {k: int(v) for k, v in (sample.get("statistics") or {}).items() if isinstance(v, int)}
    state.previous_ethtool = {k: int(v) for k, v in (sample.get("ethtool_stats") or {}).items() if isinstance(v, int)}
    state.previous_aer = {
        k: int(v) for k, v in ((sample.get("pcie") or {}).get("aer") or {}).items() if isinstance(v, int)
    }

    otp = dict(sample.get("otp") or {})
    if not otp.get("error"):
        state.golden_otp_sha = str(otp.get("sha256") or "")
        state.otp_sha = state.golden_otp_sha
    registers = dict(sample.get("registers") or {})
    if not registers.get("error"):
        state.golden_registers_sha = str(registers.get("sha256") or "")

    cursor = (sample.get("dmesg") or {}).get("cursor")
    if isinstance(cursor, (int, float)):
        state.dmesg_cursor = float(cursor)
