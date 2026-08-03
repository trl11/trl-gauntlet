"""SSD endurance — repeated write/read/verify against one or more disks.

Each tick probes every device on every unit: a ``dd`` write and read for
bandwidth, a SHA-256 write-verify, and SMART counters. Runs for a fixed
duration and passes when every unit stayed within its anomaly budget.

With ``units:`` empty the run target is the single unit. Listing units probes
them concurrently, one worker thread each, and namespaces their metrics under
``units.<name>.*``.

Anomaly kinds: ``write_verify/miscompare``, ``smart/media_error_increased``,
``smart/error_log_increased``, ``smart/critical_warning_raised``,
``bandwidth/read_below_floor``, ``bandwidth/write_below_floor``,
``ssh/cache_drop_failed``, ``ssh/probe_error``.
"""

from __future__ import annotations

import contextlib
import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from gauntlet_suite import (
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
    warn,
)
from gauntlet_suite.remote import RemoteError, capture_host_facts, connect, is_alive

from suite import probe as probe_engine
from suite.profile import SsdProfile, Unit


@dataclass
class _UnitState:
    """One unit's connection and rolling per-device accumulators."""

    name: str
    target: RemoteTarget | None = None
    devices: dict[str, probe_engine.DeviceState] = field(default_factory=dict)
    client: Any = None
    monitor: RemoteMonitor | None = None
    installed_path: str | None = None
    alive_at_end: bool = False
    probe_errors: int = 0
    facts: dict[str, str] = field(default_factory=dict)


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    units: list[_UnitState] = field(default_factory=list)
    pool: ThreadPoolExecutor | None = None


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _units(ctx: SuiteContext, profile: SsdProfile) -> list[Unit]:
    """The units to probe, defaulting to the single run target."""
    if profile.units:
        return list(profile.units)
    return [Unit(name="uut", host=ctx.target or "")]


def _setup(ctx: SuiteContext) -> None:
    profile: SsdProfile = ctx.profile
    anomalies = AnomalyLog(ctx.jsonl)
    units = _units(ctx, profile)
    state = _State(anomalies=anomalies)

    if profile.driver == "mock":
        info(f"driver=mock — {len(units)} unit(s), probe results are synthesised")

    for unit in units:
        unit_state = _UnitState(
            name=unit.name,
            devices={d.name: probe_engine.DeviceState() for d in profile.probe.devices},
        )
        if profile.driver != "mock":
            _connect_unit(unit_state, unit, profile, anomalies, ctx)
        state.units.append(unit_state)

    if len(state.units) > 1:
        state.pool = ThreadPoolExecutor(max_workers=len(state.units), thread_name_prefix="ssd-unit")

    _write_units_artifact(ctx, state)
    ctx.extras["state"] = state


def _connect_unit(
    unit_state: _UnitState,
    unit: Unit,
    profile: SsdProfile,
    anomalies: AnomalyLog,
    ctx: SuiteContext,
) -> None:
    """Open the session for one unit and prepare its disks."""
    target = RemoteTarget.from_env(host=unit.host or None)
    info(f"{unit.name}: connecting to {target.user}@{target.host}")
    client = connect(target)
    unit_state.target = target
    unit_state.client = client
    unit_state.facts = capture_host_facts(client)

    if profile.provision.enabled:
        info(f"{unit.name}: provisioning {profile.provision.device} at {profile.provision.mount_point}")
        probe_engine.provision(
            client,
            profile.provision,
            ssh_user=target.user,
            timeout=profile.probe.ssh_timeout_s,
        )
        unit_state.installed_path = probe_engine.install(
            client,
            profile.provision.install_dir,
            timeout=profile.probe.ssh_timeout_s,
        )

    baselines = probe_engine.read_smart_baseline(client, profile.probe.devices, timeout=profile.probe.ssh_timeout_s)
    for name, counters in baselines.items():
        unit_state.devices[name].smart_baseline = counters
        if not counters:
            warn(f"{unit.name}/{name}: no SMART counters available; write-verify still applies")

    if profile.monitor.enabled:
        unit_state.monitor = RemoteMonitor(
            target,
            ctx.jsonl,
            period_s=profile.monitor.sample_period_s,
            anomalies=anomalies,
            probe_name=f"monitor.{unit.name}",
        )
        unit_state.monitor.start()


def _write_units_artifact(ctx: SuiteContext, state: _State) -> None:
    """Record what each unit is, for the run record."""
    payload = {
        u.name: {"host": u.target.host if u.target else "", **u.facts} for u in state.units if u.target is not None
    }
    if payload:
        ctx.artifact("units.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        return
    for unit in state.units:
        if unit.monitor is not None:
            with contextlib.suppress(Exception):
                unit.monitor.stop()
        if unit.client is not None:
            with contextlib.suppress(Exception):
                unit.client.close()
        unit.alive_at_end = is_alive(unit.target) if unit.target is not None else True
    if state.pool is not None:
        state.pool.shutdown(wait=True)


def _probe_unit(
    unit: _UnitState,
    profile: SsdProfile,
    anomalies: AnomalyLog,
    iteration: int,
) -> dict[str, Any]:
    """Probe every device on one unit. Runs on a worker thread when fanned out."""
    result: dict[str, Any] = {"probe_ok": True}
    for device in profile.probe.devices:
        device_state = unit.devices[device.name]
        try:
            if profile.driver == "mock":
                data = probe_engine.synth_probe(device)
            else:
                data = probe_engine.run_probe(unit.client, profile.probe, device, unit.installed_path)
        except (RemoteError, TimeoutError, OSError) as exc:
            unit.probe_errors += 1
            result["probe_ok"] = False
            result[device.name] = {"error": f"{type(exc).__name__}: {exc}"}
            anomalies.record(
                "ssh",
                "probe_error",
                iteration=iteration,
                detail={"unit": unit.name, "device": device.name, "error": str(exc)},
            )
            continue

        for anomaly in probe_engine.evaluate(device_state, profile.probe, device, data):
            anomalies.record(
                anomaly.probe,
                anomaly.kind,
                iteration=iteration,
                detail={"unit": unit.name, **anomaly.detail},
            )

        if data.get("error"):
            result["probe_ok"] = False
        result[device.name] = {
            "write_mbps": data.get("write_mbps"),
            "read_mbps": data.get("read_mbps"),
            "verify_ok": data.get("verify_ok"),
            "verify_failures": device_state.verify_failures,
            **{f"smart_{k}": v for k, v in device_state.last_smart.items() if isinstance(v, (int, float))},
        }
    return result


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: SsdProfile = ctx.profile
    state = _state(ctx)

    if state.pool is not None:
        futures = {
            unit.name: state.pool.submit(_probe_unit, unit, profile, state.anomalies, ictx.iteration)
            for unit in state.units
        }
        per_unit = {name: future.result() for name, future in futures.items()}
    else:
        per_unit = {u.name: _probe_unit(u, profile, state.anomalies, ictx.iteration) for u in state.units}

    failed = [name for name, data in per_unit.items() if not data.get("probe_ok")]
    metrics: dict[str, Any] = {"anomalies_total": state.anomalies.total()}
    if len(state.units) == 1:
        # A single-unit run keeps the flat metric shape.
        metrics.update({k: v for k, v in next(iter(per_unit.values())).items() if k != "probe_ok"})
    else:
        metrics["units"] = per_unit
        metrics["units_ok"] = len(per_unit) - len(failed)

    return IterationOutcome(
        success=not failed,
        reason=f"probe failed on {', '.join(failed)}" if failed else "",
        metrics=metrics,
        summary=_tick_summary(profile, state),
    )


def _tick_summary(profile: SsdProfile, state: _State) -> str:
    chunks = []
    for unit in state.units:
        parts = [
            f"{d.name} w={unit.devices[d.name].mean_write():.0f} r={unit.devices[d.name].mean_read():.0f}"
            for d in profile.probe.devices
            if unit.devices[d.name].write_samples or unit.devices[d.name].read_samples
        ]
        if parts:
            prefix = f"{unit.name}: " if len(state.units) > 1 else ""
            chunks.append(prefix + " ".join(parts) + " MB/s")
    return "  ".join(chunks)


def _evaluate(outcomes: list[IterationOutcome], profile: SsdProfile) -> tuple[bool, str] | None:
    if not outcomes:
        return False, "no probe ticks completed"
    anomalies = int(outcomes[-1].metrics.get("anomalies_total", 0))
    if anomalies > profile.pass_criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {profile.pass_criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    _outcomes: list[IterationOutcome],
    result: RunResult,
    profile: SsdProfile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    multi = len(state.units) > 1
    rows = [
        make_result("ticks", "Probe ticks", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]
    if multi:
        rows.append(make_result("units", "Units", len(state.units), format="int"))

    for unit in state.units:
        prefix = f"{unit.name}." if multi else ""
        label = f"{unit.name} " if multi else ""
        for device in profile.probe.devices:
            device_state = unit.devices[device.name]
            rows += [
                make_result(
                    f"{prefix}{device.name}.write_mbps",
                    f"{label}{device.name} — mean write",
                    round(device_state.mean_write(), 1),
                    unit="MB/s",
                    format="decimal",
                    precision=1,
                ),
                make_result(
                    f"{prefix}{device.name}.read_mbps",
                    f"{label}{device.name} — mean read",
                    round(device_state.mean_read(), 1),
                    unit="MB/s",
                    format="decimal",
                    precision=1,
                ),
                make_result(
                    f"{prefix}{device.name}.verify_failures",
                    f"{label}{device.name} — miscompares",
                    device_state.verify_failures,
                    format="int",
                    highlight=device_state.verify_failures > profile.pass_criteria.max_verify_failures,
                ),
            ]
        rows.append(
            make_result(
                f"{prefix}alive_at_end",
                f"{label}unit alive at end".strip().capitalize(),
                "yes" if unit.alive_at_end else "no",
                highlight=not unit.alive_at_end and profile.pass_criteria.require_alive_at_end,
            )
        )
        if unit.probe_errors:
            rows.append(
                make_result(
                    f"{prefix}probe_errors",
                    f"{label}probe errors".strip().capitalize(),
                    unit.probe_errors,
                    format="int",
                    highlight=True,
                )
            )

    rows.append(
        make_result(
            "anomalies_total",
            "Total anomalies",
            state.anomalies.total(),
            format="int",
            highlight=state.anomalies.total() > profile.pass_criteria.max_anomalies,
        )
    )
    rows += [
        make_result(f"anomalies.{probe}", f"Anomalies — {probe}", count, format="int", highlight=count > 0)
        for probe, count in sorted(state.anomalies.counts().items())
    ]
    return rows


def _summary(ctx: SuiteContext, profile: SsdProfile) -> dict[str, str]:
    return {
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "units": ", ".join(u.name for u in _units(ctx, profile)),
        "devices": ", ".join(f"{d.name}={d.device}" for d in profile.probe.devices),
        "test_size_mb": str(profile.probe.test_size_mb),
        "provision": "enabled" if profile.provision.enabled else "disabled",
    }


def _hardware(ctx: SuiteContext, profile: SsdProfile) -> dict[str, dict[str, str]]:
    hardware = {u.name: {"host": u.host or ctx.target or "", "driver": profile.driver} for u in _units(ctx, profile)}
    hardware.update({d.name: {"device": d.device, "test_path": d.test_path} for d in profile.probe.devices})
    return hardware


SPEC = SuiteSpec(
    name="ssd",
    profile_model=SsdProfile,
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
