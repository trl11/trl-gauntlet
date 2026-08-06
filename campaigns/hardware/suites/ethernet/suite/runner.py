"""Ethernet throughput — timed transfers between the unit and the lab host.

At setup a pure-Python probe is installed on the unit and a TCP server is
started lab-side. Each tick the probe pushes a fixed payload up and pulls the
same amount down, timing each direction. The verdict gates on session-average
throughput.

Anomaly kinds: ``bandwidth/tx_below_floor``, ``bandwidth/rx_below_floor``,
``ssh/probe_error``, ``probe/reported_error``.
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
    RemoteMonitor,
    RemoteTarget,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
)
from gauntlet_sdk.remote import RemoteError, capture_host_facts, connect, is_alive, run, shell_quote

from suite.profile import EthernetProfile
from suite.server import ThroughputServer

PROBE_SCRIPT = Path(__file__).parent / "probe_script.py"


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    target: RemoteTarget | None
    client: Any = None
    monitor: RemoteMonitor | None = None
    server: ThroughputServer | None = None
    remote_path: str = ""
    lab_host: str = ""
    tx_samples: list[float] = field(default_factory=list)
    rx_samples: list[float] = field(default_factory=list)
    probe_errors: int = 0
    alive_at_end: bool = False

    def mean_tx(self) -> float:
        return sum(self.tx_samples) / len(self.tx_samples) if self.tx_samples else 0.0

    def mean_rx(self) -> float:
        return sum(self.rx_samples) / len(self.rx_samples) if self.rx_samples else 0.0


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _install_probe(client: Any, install_dir: str, *, timeout: float) -> str:
    """Copy the probe onto the unit and return its remote path."""
    remote_path = f"{install_dir.rstrip('/')}/ethernet_probe.py"
    command = (
        f"mkdir -p {shell_quote(install_dir)} && "
        f"printf '%s' {shell_quote(PROBE_SCRIPT.read_text())} > {shell_quote(remote_path)}"
    )
    result = run(client, command, timeout=timeout)
    if not result.ok:
        raise RemoteError(f"installing probe to {remote_path}: {result.output}")
    return remote_path


def _local_ssh_address(client: Any) -> str:
    """The lab address the unit sees, taken from the local end of the SSH socket."""
    transport = client.get_transport()
    if transport is None:
        return ""
    sock = getattr(transport, "sock", None)
    if sock is None:
        return ""
    with contextlib.suppress(OSError, AttributeError, IndexError):
        return str(sock.getsockname()[0])
    return ""


def _setup(ctx: SuiteContext) -> None:
    profile: EthernetProfile = ctx.profile
    anomalies = AnomalyLog(ctx.jsonl)

    if profile.driver == "mock":
        info("driver=mock — no unit contacted, throughput is synthesised")
        ctx.extras["state"] = _State(anomalies=anomalies, target=None)
        return

    target = RemoteTarget.from_env(host=ctx.target)
    info(f"connecting to {target.user}@{target.host}")
    client = connect(target)

    facts = capture_host_facts(client)
    if facts:
        ctx.artifact("uut.json").write_text(json.dumps(facts, indent=2, sort_keys=True) + "\n")

    server = ThroughputServer(profile.probe.server_port, connection_timeout_s=profile.probe.socket_timeout_s * 2)
    server.start()
    lab_host = profile.probe.lab_host or _local_ssh_address(client)
    if not lab_host:
        server.stop()
        client.close()
        raise RemoteError("cannot determine the lab address the unit should connect to; set probe.lab_host")
    info(f"throughput server on {lab_host}:{server.bound_port}")

    remote_path = _install_probe(client, profile.probe.install_dir, timeout=profile.probe.ssh_timeout_s)

    monitor = None
    if profile.monitor.enabled:
        monitor = RemoteMonitor(target, ctx.jsonl, period_s=profile.monitor.sample_period_s, anomalies=anomalies)
        monitor.start()

    ctx.extras["state"] = _State(
        anomalies=anomalies,
        target=target,
        client=client,
        monitor=monitor,
        server=server,
        remote_path=remote_path,
        lab_host=lab_host,
    )


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    for closeable in (state.monitor, state.server):
        if closeable is not None:
            with contextlib.suppress(Exception):
                closeable.stop()
    if state.client is not None:
        with contextlib.suppress(Exception):
            state.client.close()
    state.alive_at_end = is_alive(state.target) if state.target is not None else True


def _run_probe(state: _State, profile: EthernetProfile) -> dict[str, Any]:
    """One speed test on the unit, returning the probe's parsed JSON."""
    nbytes = int(profile.probe.transfer_mb) * 1024 * 1024
    assert state.server is not None
    command = (
        f"python3 {shell_quote(state.remote_path)} {shell_quote(state.lab_host)} "
        f"{state.server.bound_port} {nbytes} {profile.probe.socket_timeout_s}"
    )
    result = run(state.client, command, timeout=profile.probe.ssh_timeout_s)
    text = result.stdout.strip()
    if not text:
        raise RemoteError(f"probe produced no output: {result.stderr.strip()[-300:]}")
    try:
        return dict(json.loads(text.splitlines()[-1]))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise RemoteError(f"probe output was not JSON: {exc}") from exc


def _synth_probe(iteration: int) -> dict[str, Any]:
    """Synthetic throughput for the mock driver, varied per tick."""
    drift = (iteration % 5) * 3.0
    return {"tx_mbps": 920.0 - drift, "rx_mbps": 940.0 - drift, "error": None}


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: EthernetProfile = ctx.profile
    state = _state(ctx)

    try:
        data = _synth_probe(ictx.iteration) if profile.driver == "mock" else _run_probe(state, profile)
    except (RemoteError, TimeoutError, OSError) as exc:
        state.probe_errors += 1
        reason = f"{type(exc).__name__}: {exc}"
        state.anomalies.record("ssh", "probe_error", iteration=ictx.iteration, detail={"error": reason})
        return IterationOutcome(success=False, reason=reason, metrics={"anomalies_total": state.anomalies.total()})

    if data.get("error"):
        state.anomalies.record(
            "probe", "reported_error", iteration=ictx.iteration, detail={"error": str(data["error"])}
        )

    tx, rx = data.get("tx_mbps"), data.get("rx_mbps")
    for value, samples, floor, kind in (
        (tx, state.tx_samples, profile.probe.tx_floor_mbps, "tx_below_floor"),
        (rx, state.rx_samples, profile.probe.rx_floor_mbps, "rx_below_floor"),
    ):
        if not isinstance(value, (int, float)):
            continue
        samples.append(float(value))
        if floor is not None and value < floor:
            state.anomalies.record(
                "bandwidth",
                kind,
                iteration=ictx.iteration,
                detail={"observed_mbps": value, "floor_mbps": floor},
            )

    measured = isinstance(tx, (int, float)) and isinstance(rx, (int, float))
    return IterationOutcome(
        success=measured and not data.get("error"),
        reason=str(data.get("error") or ("probe returned no measurement" if not measured else "")),
        metrics={
            "tx_mbps": tx,
            "rx_mbps": rx,
            "mean_tx_mbps": round(state.mean_tx(), 2),
            "mean_rx_mbps": round(state.mean_rx(), 2),
            "anomalies_total": state.anomalies.total(),
        },
        summary=f"tx={tx:.0f} rx={rx:.0f} Mbps" if measured else "no measurement",
    )


def _evaluate(outcomes: list[IterationOutcome], profile: EthernetProfile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no speed tests completed"
    last = outcomes[-1].metrics
    mean_tx = float(last.get("mean_tx_mbps") or 0.0)
    mean_rx = float(last.get("mean_rx_mbps") or 0.0)
    criteria = profile.pass_criteria
    if mean_tx < criteria.min_avg_tx_mbps:
        return False, f"average transmit {mean_tx:.1f} Mbps below floor {criteria.min_avg_tx_mbps:.1f}"
    if mean_rx < criteria.min_avg_rx_mbps:
        return False, f"average receive {mean_rx:.1f} Mbps below floor {criteria.min_avg_rx_mbps:.1f}"
    anomalies = int(last.get("anomalies_total", 0))
    if anomalies > criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    _outcomes: list[IterationOutcome],
    result: RunResult,
    profile: EthernetProfile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    rows = [
        make_result("tests", "Speed tests", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result(
            "mean_tx_mbps",
            "Mean transmit",
            round(state.mean_tx(), 1),
            unit="Mbps",
            format="decimal",
            precision=1,
            highlight=state.mean_tx() < profile.pass_criteria.min_avg_tx_mbps,
        ),
        make_result(
            "mean_rx_mbps",
            "Mean receive",
            round(state.mean_rx(), 1),
            unit="Mbps",
            format="decimal",
            precision=1,
            highlight=state.mean_rx() < profile.pass_criteria.min_avg_rx_mbps,
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
        make_result("probe_errors", "Probe errors", state.probe_errors, format="int", highlight=state.probe_errors > 0),
        make_result(
            "anomalies_total",
            "Total anomalies",
            state.anomalies.total(),
            format="int",
            highlight=state.anomalies.total() > profile.pass_criteria.max_anomalies,
        ),
        make_result(
            "alive_at_end",
            "Unit alive at end",
            "yes" if state.alive_at_end else "no",
            highlight=not state.alive_at_end and profile.pass_criteria.require_alive_at_end,
        ),
    ]
    rows += [
        make_result(f"anomalies.{probe}", f"Anomalies — {probe}", count, format="int", highlight=count > 0)
        for probe, count in sorted(state.anomalies.counts().items())
    ]
    return rows


def _summary(_ctx: SuiteContext, profile: EthernetProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "transfer_mb": str(profile.probe.transfer_mb),
        "server_port": str(profile.probe.server_port),
    }


def _hardware(ctx: SuiteContext, profile: EthernetProfile) -> dict[str, dict[str, str]]:
    return {"uut": {"driver": profile.driver, "host": ctx.target or ""}}


SPEC = SuiteSpec(
    name="ethernet",
    profile_model=EthernetProfile,
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
