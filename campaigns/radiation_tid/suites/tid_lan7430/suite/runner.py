"""LAN7430 Ethernet Controller under total ionising dose.

Component      LAN7430-I/Y9X
Test vehicle   EVB-LAN7430
Host           Raspberry Pi
Fixture        1-1

Each tick measures throughput across the controller with iperf3 and then reads
everything about the part that dose could move: link state and negotiated
speed, the kernel and driver error counters, the PCIe link width and its AER
counters, the OTP image and the register dump, die temperature, and the bench
supply current when the bench has a supply.

The server end of the throughput test runs on the unit bound to the
controller's own address, so the traffic has to cross the part rather than the
host's built-in interface. Because that is the assumption the whole
measurement rests on, every tick also checks the interface's own byte counters
moved by roughly what iperf3 reported, and says so when they did not.

Nothing here aborts. The part is expected to degrade and eventually fail, so a
lost link, a collapsed measurement or a changed OTP image is recorded against
the tick it appeared at and the session carries on — a part that recovers as
it anneals is as much of a result as one that dies.

Anomaly kinds: ``collector/*``, ``counters/*``, ``iperf/measurement_failed``,
``kernel/message``, ``link/down``, ``link/interface_missing``,
``link/mac_changed``, ``link/speed_degraded``, ``otp/changed``,
``otp/unreadable``, ``pcie/aer_correctable``, ``pcie/aer_fatal``,
``pcie/aer_nonfatal``, ``pcie/device_missing``, ``pcie/link_degraded``,
``psu/unreadable``, ``registers/changed``, ``ssh/unreachable``,
``topology/traffic_bypassed_interface``.
"""

from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_sdk import (
    AnomalyLog,
    IterationContext,
    IterationOutcome,
    PhaseRecord,
    PhaseTimer,
    RemoteTarget,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
    warn,
)
from gauntlet_sdk.remote import RemoteError, capture_host_facts, connect, is_alive, run, shell_quote

from suite import iperf, mock, telemetry
from suite.profile import TidLan7430Profile
from suite.psu import PsuReader

COLLECTOR_SOURCE = Path(__file__).parent / "collector_script.py"

# The engineering key, carried as a submodule so a bench run needs nothing
# exported and nothing copied into ~/.ssh. Relative to the repository root,
# which is found by walking up from here.
BUNDLED_KEY = Path("extras/trl-engineering-keys/saver/id_ed_saver_eng_key")

# A tick whose interface counters moved by less than this fraction of what
# iperf3 reported was carried somewhere other than the part under test.
TRAFFIC_MATCH_FLOOR = 0.5


def _bundled_key() -> str:
    """The engineering key from the submodule, or empty when it is not checked out.

    Searched upward from this file so it is found wherever the campaign sits in
    the tree, and missing without complaint on a bench that installs its own.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / BUNDLED_KEY
        if candidate.is_file():
            return str(candidate)
    return ""


def _ssh_target(profile: TidLan7430Profile, host: str | None) -> RemoteTarget:
    """Where to log in, taking the login from the profile rather than the environment.

    The profile names the user, so a run started from the app no longer depends
    on what was exported into the shell that launched the server. A key the
    profile does not name comes from the submodule, and failing that from
    whatever `GAUNTLET_SSH_KEY` and the usual `~/.ssh` candidates resolve to,
    so a bench holding its own key still works.
    """
    from_env = RemoteTarget.from_env(host=host)
    configured = str(Path(profile.ssh_key_path).expanduser()) if profile.ssh_key_path else ""
    return RemoteTarget(
        host=from_env.host,
        user=profile.ssh_user,
        key_path=configured or _bundled_key() or from_env.key_path,
    )


class IperfMissing(RuntimeError):
    """The lab host has no iperf3 client, so nothing can be measured."""


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    telemetry: telemetry.TelemetryState
    address: str = ""
    alive_at_end: bool = False
    client: Any = None
    collector_path: str = ""
    link_up_at_end: bool = False
    measurement_failures: int = 0
    psu: PsuReader | None = None
    rx_samples: list[float] = field(default_factory=list)
    server: iperf.ServerHandle | None = None
    target: RemoteTarget | None = None
    tx_samples: list[float] = field(default_factory=list)
    udp_loss_samples: list[float] = field(default_factory=list)

    def mean_tx(self) -> float:
        return sum(self.tx_samples) / len(self.tx_samples) if self.tx_samples else 0.0

    def mean_rx(self) -> float:
        return sum(self.rx_samples) / len(self.rx_samples) if self.rx_samples else 0.0

    def mean_udp_loss(self) -> float:
        return sum(self.udp_loss_samples) / len(self.udp_loss_samples) if self.udp_loss_samples else 0.0


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _resolve_interface_address(client: Any, interface: str, *, timeout: float) -> str:
    """Ask the unit which address the controller's interface holds.

    Read from the unit rather than configured, so the profile does not have to
    be edited when the bench hands out a different lease.
    """
    command = f"ip -4 -oneline address show dev {shell_quote(interface)}"
    result = run(client, command, timeout=timeout)
    for line in result.stdout.splitlines():
        fields = line.split()
        if "inet" in fields:
            return fields[fields.index("inet") + 1].split("/")[0]
    return ""


def _setup_mock(ctx: SuiteContext) -> None:
    """A session with no hardware, baselined against a healthy synthetic part."""
    info("driver=mock — no unit contacted, the part and its throughput are synthesised")
    profile: TidLan7430Profile = ctx.profile
    telemetry_state = telemetry.TelemetryState()
    telemetry.establish_baseline(mock.sample(0, profile.interface.name), telemetry_state)
    ctx.extras["state"] = _State(anomalies=AnomalyLog(ctx.jsonl), telemetry=telemetry_state)


def _setup(ctx: SuiteContext) -> None:
    profile: TidLan7430Profile = ctx.profile
    if profile.driver == "mock":
        _setup_mock(ctx)
        return

    if not iperf.client_available():
        raise IperfMissing(
            "iperf3 is not installed on this host, and the throughput measurement is run from here. "
            "Install it lab-side (apt install iperf3) and on the unit."
        )

    anomalies = AnomalyLog(ctx.jsonl)
    target = _ssh_target(profile, ctx.target)
    info(f"connecting to {target.user}@{target.host} with {target.key_path}")
    client = connect(target)

    facts = capture_host_facts(client)
    if facts:
        ctx.artifact("uut.json").write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n")

    address = profile.interface.address or _resolve_interface_address(
        client, profile.interface.name, timeout=profile.ssh_timeout_s
    )
    if not address:
        client.close()
        raise RemoteError(
            f"{profile.interface.name} has no IPv4 address on the unit; "
            "set interface.address in the profile if it is configured elsewhere"
        )
    if address == target.host:
        # Reaching the unit through the part under test means the SSH session
        # dies with it, taking the measurement of its own failure with it.
        warn(f"the run target and {profile.interface.name} are both {address}: control traffic shares the part")

    collector_path = telemetry.install_collector(
        client, profile.install_dir, COLLECTOR_SOURCE.read_text(), timeout=profile.ssh_timeout_s
    )

    telemetry_state = telemetry.TelemetryState()
    baseline = telemetry.collect(
        client,
        collector_path,
        telemetry.collector_config(profile, telemetry_state),
        timeout=profile.ssh_timeout_s,
    )
    ctx.artifact("lan7430-baseline.json").write_text(json.dumps(baseline, indent=2, sort_keys=True) + "\n")
    telemetry.establish_baseline(baseline, telemetry_state)
    _report_baseline(baseline, profile)

    server = iperf.start_server(client, profile, address)
    info(f"iperf3 server on {address}:{server.port}, measuring across {profile.interface.name}")

    reader = None
    if profile.psu.enabled:
        reader = PsuReader.discover(ctx.env.api_base, profile.psu.capability, timeout_s=profile.psu.timeout_s)
        info("bench supply found, logging its readings" if reader else "no bench supply on this bench, carrying on")

    ctx.extras["state"] = _State(
        address=address,
        anomalies=anomalies,
        client=client,
        collector_path=collector_path,
        psu=reader,
        server=server,
        target=target,
        telemetry=telemetry_state,
    )


def _report_baseline(baseline: dict[str, Any], profile: TidLan7430Profile) -> None:
    """Say what the part looked like before exposure, in the run log."""
    link = baseline.get("link") or {}
    driver = baseline.get("driver") or {}
    speed = link.get("speed_mbps")
    info(
        f"baseline: {driver.get('driver', 'unknown driver')} on {driver.get('bus_info', 'unknown slot')}, "
        f"link {link.get('operstate', 'unknown')} at {speed or 'unknown'} Mbps, mac {link.get('address', 'unknown')}"
    )
    if isinstance(speed, (int, float)) and speed < profile.interface.expected_speed_mbps:
        warn(f"link came up at {speed} Mbps, below the expected {profile.interface.expected_speed_mbps}")
    for probe, message in (baseline.get("errors") or {}).items():
        warn(f"baseline: {probe} unreadable: {message}")


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    if state.client is not None and state.server is not None:
        with contextlib.suppress(Exception):
            iperf.stop_server(state.client, state.server)
    if state.client is not None:
        with contextlib.suppress(Exception):
            state.client.close()
    state.alive_at_end = is_alive(state.target) if state.target is not None else True


def _reconnect(state: _State, profile: TidLan7430Profile) -> bool:
    """Open a fresh SSH session after one died. Answers whether it worked.

    A unit that reboots, or a link that drops while the control path shares
    it, loses the session. Reconnecting rather than ending the run is what
    lets the session record the part coming back.
    """
    if state.target is None:
        return False
    with contextlib.suppress(Exception):
        if state.client is not None:
            state.client.close()
    try:
        state.client = connect(state.target)
    except (RemoteError, OSError):
        state.client = None
        return False
    with contextlib.suppress(RemoteError, TimeoutError, OSError):
        state.collector_path = telemetry.install_collector(
            state.client, profile.install_dir, COLLECTOR_SOURCE.read_text(), timeout=profile.ssh_timeout_s
        )
    return True


def _ensure_server(state: _State, profile: TidLan7430Profile, iteration: int) -> None:
    """Restart the throughput server if it is no longer listening."""
    if state.client is None or state.server is None:
        return
    if iperf.server_alive(state.client, state.server, timeout=profile.ssh_timeout_s):
        return
    state.anomalies.record("iperf", "server_restarted", iteration=iteration, detail={"address": state.address})
    with contextlib.suppress(iperf.IperfError, RemoteError, TimeoutError, OSError):
        state.server = iperf.start_server(state.client, profile, state.address)


def _measure(state: _State, profile: TidLan7430Profile, iteration: int) -> dict[str, dict[str, float]]:
    """Both TCP directions and the UDP pass, each recorded as it succeeds.

    A direction that fails leaves its entry out rather than ending the tick:
    a part that can still receive but no longer transmit is a result worth
    keeping, and reporting nothing would lose it.
    """
    assert state.server is not None
    measurements: dict[str, dict[str, float]] = {}
    attempts: list[tuple[str, Any]] = [
        ("tcp_tx", lambda: iperf.measure_tcp(state.server, profile, reverse=True)),
        ("tcp_rx", lambda: iperf.measure_tcp(state.server, profile, reverse=False)),
    ]
    if profile.iperf.udp_enabled:
        attempts.append(("udp", lambda: iperf.measure_udp(state.server, profile)))

    for name, attempt in attempts:
        try:
            measurements[name] = attempt()
        except iperf.IperfError as exc:
            state.measurement_failures += 1
            state.anomalies.record(
                "iperf",
                "measurement_failed",
                iteration=iteration,
                detail={"direction": name, "error": str(exc)[:300]},
            )
    return measurements


def _check_traffic_crossed_part(
    state: _State,
    measurements: dict[str, dict[str, float]],
    counters: dict[str, Any],
    profile: TidLan7430Profile,
    iteration: int,
) -> None:
    """Confirm the interface carried what iperf3 says it moved.

    The measurement is only about the LAN7430 if the traffic went over it.
    On a bench where the unit can reach the lab by more than one route, it may
    not have, and a throughput number from the host's built-in interface would
    otherwise look like a healthy result.
    """
    moved = counters.get("bytes_step")
    if not isinstance(moved, (int, float)) or moved <= 0:
        return
    expected_bits = sum(
        result.get("mbps", 0.0) * 1e6 * seconds
        for result, seconds in (
            (measurements.get("tcp_tx", {}), profile.iperf.stream_s),
            (measurements.get("tcp_rx", {}), profile.iperf.stream_s),
            (measurements.get("udp", {}), profile.iperf.udp_stream_s),
        )
    )
    expected_bytes = expected_bits / 8.0
    if expected_bytes <= 0:
        return
    if moved < expected_bytes * TRAFFIC_MATCH_FLOOR:
        state.anomalies.record(
            "topology",
            "traffic_bypassed_interface",
            iteration=iteration,
            detail={
                "interface": profile.interface.name,
                "interface_bytes": moved,
                "iperf_bytes": round(expected_bytes),
            },
        )


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: TidLan7430Profile = ctx.profile
    state = _state(ctx)
    phases: list[PhaseRecord] = []

    if profile.driver == "mock":
        measurements = mock.throughput(ictx.iteration)
        sample = mock.sample(ictx.iteration, profile.interface.name)
    else:
        if state.client is None and not _reconnect(state, profile):
            state.anomalies.record("ssh", "unreachable", iteration=ictx.iteration, detail={})
            return IterationOutcome(
                success=False,
                reason="unit unreachable over ssh",
                metrics={"present": 0, "anomalies_total": state.anomalies.total()},
                summary="unreachable",
            )
        with PhaseTimer("throughput", phases) as phase:
            phase.set_detail(interface=profile.interface.name, address=state.address)
            _ensure_server(state, profile, ictx.iteration)
            measurements = _measure(state, profile, ictx.iteration)
        with PhaseTimer("telemetry", phases) as phase:
            phase.set_detail(interface=profile.interface.name)
            sample = _read_telemetry(state, profile, ictx.iteration)

    observed = telemetry.analyse(sample, state.telemetry, profile, ictx.iteration, state.anomalies)
    if profile.driver != "mock":
        _check_traffic_crossed_part(state, measurements, observed.get("counters", {}), profile, ictx.iteration)

    return _outcome(state, profile, measurements, observed, phases, ictx.iteration)


def _read_telemetry(state: _State, profile: TidLan7430Profile, iteration: int) -> dict[str, Any]:
    """One collector run, turned into an anomaly rather than an exception."""
    try:
        return telemetry.collect(
            state.client,
            state.collector_path,
            telemetry.collector_config(profile, state.telemetry),
            timeout=profile.ssh_timeout_s,
        )
    except (RemoteError, TimeoutError, OSError) as exc:
        state.anomalies.record("ssh", "unreachable", iteration=iteration, detail={"error": str(exc)[:300]})
        # The session is suspect once a command fails on it, so the next tick
        # opens a fresh one rather than reusing this.
        with contextlib.suppress(Exception):
            state.client.close()
        state.client = None
        return {"present": False, "interface": profile.interface.name, "errors": {"ssh": str(exc)[:300]}}


def _check_floors(
    state: _State,
    profile: TidLan7430Profile,
    tx: float | None,
    rx: float | None,
    udp: dict[str, float],
    iteration: int,
) -> None:
    """Flag a tick that fell below the profile's per-tick limits.

    Separate from the session gates in ``pass_criteria``: this is what marks
    the tick a part started degrading at, which an average taken over the whole
    session cannot show.
    """
    for value, floor, kind in (
        (tx, profile.iperf.tx_floor_mbps, "tx_below_floor"),
        (rx, profile.iperf.rx_floor_mbps, "rx_below_floor"),
    ):
        if floor is not None and isinstance(value, (int, float)) and value < floor:
            state.anomalies.record(
                "bandwidth",
                kind,
                iteration=iteration,
                detail={"floor_mbps": floor, "observed_mbps": value},
            )

    ceiling = profile.iperf.udp_loss_ceiling_pct
    loss = udp.get("loss_pct")
    if ceiling is not None and isinstance(loss, (int, float)) and loss > ceiling:
        state.anomalies.record(
            "bandwidth",
            "udp_loss_above_ceiling",
            iteration=iteration,
            detail={"ceiling_pct": ceiling, "observed_pct": loss},
        )


def _outcome(
    state: _State,
    profile: TidLan7430Profile,
    measurements: dict[str, dict[str, float]],
    observed: dict[str, Any],
    phases: list[PhaseRecord],
    iteration: int,
) -> IterationOutcome:
    """Fold this tick's measurements and telemetry into one record."""
    tx = measurements.get("tcp_tx", {}).get("mbps")
    rx = measurements.get("tcp_rx", {}).get("mbps")
    udp = measurements.get("udp", {})

    if isinstance(tx, (int, float)):
        state.tx_samples.append(float(tx))
    if isinstance(rx, (int, float)):
        state.rx_samples.append(float(rx))
    if isinstance(udp.get("loss_pct"), (int, float)):
        state.udp_loss_samples.append(float(udp["loss_pct"]))
    _check_floors(state, profile, tx, rx, udp, iteration)
    state.link_up_at_end = bool(observed.get("link", {}).get("up"))

    metrics: dict[str, Any] = dict(observed)
    metrics["throughput"] = {
        "mean_rx_mbps": round(state.mean_rx(), 2),
        "mean_tx_mbps": round(state.mean_tx(), 2),
        "retransmits": measurements.get("tcp_tx", {}).get("retransmits"),
        "rx_mbps": rx,
        "tx_mbps": tx,
    }
    if udp:
        metrics["udp"] = {
            "jitter_ms": udp.get("jitter_ms"),
            "loss_pct": udp.get("loss_pct"),
        }
    if state.psu is not None:
        reading = state.psu.read()
        if reading:
            metrics["psu"] = reading
    metrics["anomalies_total"] = state.anomalies.total()

    measured = isinstance(tx, (int, float)) or isinstance(rx, (int, float))
    summary = f"tx={tx or 0:.0f} rx={rx or 0:.0f} Mbps" if measured else "no measurement"
    if not observed.get("link", {}).get("up", 0):
        summary += " link down"

    return IterationOutcome(
        success=measured and bool(observed.get("present")),
        reason="" if measured else "no throughput measurement completed",
        metrics=metrics,
        phase_records=phases,
        summary=summary,
    )


def _evaluate(outcomes: list[IterationOutcome], profile: TidLan7430Profile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no ticks completed"
    criteria = profile.pass_criteria
    last = outcomes[-1].metrics
    throughput = last.get("throughput", {})
    mean_tx = float(throughput.get("mean_tx_mbps") or 0.0)
    mean_rx = float(throughput.get("mean_rx_mbps") or 0.0)

    if criteria.require_measurement and not any(outcome.success for outcome in outcomes):
        return False, "no tick produced a throughput measurement"
    if mean_tx < criteria.min_avg_tx_mbps:
        return False, f"average transmit {mean_tx:.1f} Mbps below floor {criteria.min_avg_tx_mbps:.1f}"
    if mean_rx < criteria.min_avg_rx_mbps:
        return False, f"average receive {mean_rx:.1f} Mbps below floor {criteria.min_avg_rx_mbps:.1f}"

    losses = [
        float(value)
        for outcome in outcomes
        if isinstance(value := outcome.metrics.get("udp", {}).get("loss_pct"), (int, float))
    ]
    if losses:
        mean_loss = sum(losses) / len(losses)
        if mean_loss > criteria.max_udp_loss_pct:
            return False, f"average UDP loss {mean_loss:.2f}% above ceiling {criteria.max_udp_loss_pct:.2f}%"

    if criteria.require_otp_stable and any(outcome.metrics.get("otp_matches_baseline") == 0 for outcome in outcomes):
        return False, "the OTP image read back changed during the session"
    if criteria.require_link_at_end and not last.get("link", {}).get("up"):
        return False, "the link was down when the session ended"

    anomalies = int(last.get("anomalies_total", 0))
    if anomalies > criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidLan7430Profile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    criteria = profile.pass_criteria
    udp_loss = state.mean_udp_loss()
    rows = [
        make_result("ticks", "Ticks", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result(
            "mean_tx_mbps",
            "Mean transmit",
            round(state.mean_tx(), 1),
            unit="Mbps",
            format="decimal",
            precision=1,
            highlight=state.mean_tx() < criteria.min_avg_tx_mbps,
        ),
        make_result(
            "mean_rx_mbps",
            "Mean receive",
            round(state.mean_rx(), 1),
            unit="Mbps",
            format="decimal",
            precision=1,
            highlight=state.mean_rx() < criteria.min_avg_rx_mbps,
        ),
        make_result(
            "min_tx_mbps",
            "Slowest transmit",
            round(min(state.tx_samples, default=0.0), 1),
            unit="Mbps",
            format="decimal",
            precision=1,
        ),
        make_result(
            "min_rx_mbps",
            "Slowest receive",
            round(min(state.rx_samples, default=0.0), 1),
            unit="Mbps",
            format="decimal",
            precision=1,
        ),
        make_result(
            "mean_udp_loss_pct",
            "Mean UDP loss",
            round(udp_loss, 3),
            unit="%",
            format="decimal",
            precision=3,
            highlight=udp_loss > criteria.max_udp_loss_pct,
        ),
        make_result("mac_address", "MAC address", state.telemetry.golden_mac or "unknown"),
        make_result(
            "mac_address_at_end",
            "MAC address at end",
            state.telemetry.mac or "unknown",
            highlight=state.telemetry.mac_changes > 0,
        ),
        make_result(
            "otp_sha256",
            "OTP image (sha256)",
            state.telemetry.golden_otp_sha or "unreadable",
            highlight=not state.telemetry.golden_otp_sha,
        ),
        make_result(
            "otp_sha256_at_end",
            "OTP image at end (sha256)",
            state.telemetry.otp_sha or "unreadable",
            highlight=state.telemetry.otp_sha != state.telemetry.golden_otp_sha,
        ),
        make_result(
            "otp_changes",
            "OTP image changed",
            state.telemetry.otp_changes,
            format="int",
            highlight=state.telemetry.otp_changes > 0,
        ),
        make_result(
            "register_changes",
            "Register dump changed",
            state.telemetry.register_changes,
            format="int",
            highlight=state.telemetry.register_changes > 0,
        ),
        make_result(
            "measurement_failures",
            "Failed measurements",
            state.measurement_failures,
            format="int",
            highlight=state.measurement_failures > 0,
        ),
        make_result(
            "link_up_at_end",
            "Link up at end",
            "yes" if state.link_up_at_end else "no",
            highlight=not state.link_up_at_end,
        ),
        make_result(
            "alive_at_end",
            "Unit alive at end",
            "yes" if state.alive_at_end else "no",
            highlight=not state.alive_at_end,
        ),
        make_result(
            "first_failure_tick",
            "First failing tick",
            _first_failure(outcomes),
            format="int",
            highlight=_first_failure(outcomes) >= 0,
        ),
        make_result(
            "anomalies_total",
            "Total anomalies",
            state.anomalies.total(),
            format="int",
            highlight=state.anomalies.total() > criteria.max_anomalies,
        ),
    ]
    rows += [
        make_result(f"anomalies.{probe}", f"Anomalies — {probe}", count, format="int", highlight=count > 0)
        for probe, count in sorted(state.anomalies.counts().items())
    ]
    return rows


def _first_failure(outcomes: list[IterationOutcome]) -> int:
    """The first tick that produced no measurement, or -1 if none did.

    In a dose run this is the headline number: it is where the part stopped
    working, and the dose rate turns it into a dose.
    """
    for index, outcome in enumerate(outcomes):
        if not outcome.success:
            return index
    return -1


def _summary(_ctx: SuiteContext, profile: TidLan7430Profile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "interface": profile.interface.name,
        "iperf_port": str(profile.iperf.port),
        "sample_period_s": str(profile.sample_period_s),
        "stream_s": str(profile.iperf.stream_s),
        "udp_enabled": str(profile.iperf.udp_enabled),
    }


def _hardware(ctx: SuiteContext, profile: TidLan7430Profile) -> dict[str, dict[str, str]]:
    state = ctx.extras.get("state")
    address = state.address if isinstance(state, _State) else ""
    # The MAC is the part's own identity rather than the bench's, so it is the
    # one field here that says which controller a run was taken on.
    mac = state.telemetry.golden_mac if isinstance(state, _State) else ""
    return {
        "uut": {
            "component": "LAN7430-I/Y9X",
            "driver": profile.driver,
            "host": ctx.target or "",
            "interface": profile.interface.name,
            "interface_address": address,
            "mac_address": mac,
        }
    }


SPEC = SuiteSpec(
    name="tid_lan7430",
    profile_model=TidLan7430Profile,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: float(p.duration_s),
    sample_period_seconds=lambda p: float(p.sample_period_s),
    setup=_setup,
    teardown=_teardown,
    profile_summary=_summary,
    hardware_summary=_hardware,
    verdict_results=_results,
)
