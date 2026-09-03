"""NVMe SSD storage under total ionising dose.

Component      TBD
Test vehicle   TBD
Host           Raspberry Pi
Fixture        TBD

Each tick probes every device on every unit: a ``dd`` write and read for
bandwidth, a SHA-256 write-verify that catches the disk returning something
other than what was written, the whole NVMe health log, and the kernel messages
the drive has logged since the last tick. The bench supply current is recorded
beside them when the bench has a supply.

With ``units:`` empty the run target is the single unit and the metrics keep a
flat shape. Listing units probes them concurrently, one worker thread each, and
namespaces their metrics under ``units.<name>.*`` — which is how several parts
are characterised in one exposure.

Nothing here aborts. The part is expected to degrade and eventually fail, so a
sagging rate, a block that came back corrupt, a spare pool draining or a drive
that has left the bus is recorded against the tick it appeared at and the
session carries on. A drive that recovers as it anneals is as much of a result
as one that dies, and neither is visible if the run stops at the first fault.

Every anomaly is raised through :func:`suite.anomaly.flag`, which records it in
``events.jsonl`` and warns in the run log what it means, because the log is
what an operator watches while the beam is on.

Anomaly kinds: ``bandwidth/read_below_floor``, ``bandwidth/write_below_floor``,
``device/missing``, ``kernel/message``, ``smart/critical_warning_raised``,
``smart/error_log_increased``, ``smart/media_error_increased``,
``smart/spare_below_threshold``, ``smart/spare_depleting``,
``smart/unsafe_shutdown``, ``smart/wear_increased``, ``ssh/cache_drop_failed``,
``ssh/probe_error``, ``write_verify/miscompare``.
"""

from __future__ import annotations

import contextlib
import json
from concurrent.futures import ThreadPoolExecutor
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
    warn,
)
from gauntlet_sdk.remote import RemoteError, capture_host_facts, connect, is_alive

from suite import mock
from suite import probe as probe_engine
from suite.anomaly import flag
from suite.profile import TidSsdProfile, Unit
from suite.psu import PsuReader

# The engineering key, carried as a submodule so a bench run needs nothing
# copied into ~/.ssh.
BUNDLED_KEY = Path("extras/trl-engineering-keys/saver/id_ed_saver_eng_key")


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
    dmesg_cursor: str = ""
    facts: dict[str, str] = field(default_factory=dict)


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    units: list[_UnitState] = field(default_factory=list)
    pool: ThreadPoolExecutor | None = None
    psu: PsuReader | None = None
    measured_ticks: int = 0
    first_anomaly_iteration: int | None = None


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


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


def _ssh_target(profile: TidSsdProfile, host: str | None) -> RemoteTarget:
    """Where to log in, taking the login from the profile rather than the environment.

    A run started from the app inherits whatever shell launched the server, so a
    login that lives only in an exported variable is one nobody remembers to
    set. A key the profile does not name comes from the submodule, and failing
    that from whatever `GAUNTLET_SSH_KEY` and the usual `~/.ssh` candidates
    resolve to, so a bench holding its own key still works.
    """
    from_env = RemoteTarget.from_env(host=host)
    configured = str(Path(profile.ssh_key_path).expanduser()) if profile.ssh_key_path else ""
    return RemoteTarget(
        host=from_env.host,
        user=profile.ssh_user,
        key_path=configured or _bundled_key() or from_env.key_path,
    )


def _units(ctx: SuiteContext, profile: TidSsdProfile) -> list[Unit]:
    """The units to probe, defaulting to the single run target."""
    if profile.units:
        return list(profile.units)
    return [Unit(name="uut", host=ctx.target or "")]


def _setup(ctx: SuiteContext) -> None:
    profile: TidSsdProfile = ctx.profile
    anomalies = AnomalyLog(ctx.jsonl)
    units = _units(ctx, profile)
    state = _State(anomalies=anomalies)

    if profile.driver == "mock":
        info(f"driver=mock — {len(units)} unit(s), probe results are synthesised from a degrading disk")

    for unit in units:
        unit_state = _UnitState(
            name=unit.name,
            devices={d.name: probe_engine.DeviceState() for d in profile.probe.devices},
        )
        if profile.driver != "mock":
            _connect_unit(unit_state, unit, profile, anomalies, ctx)
        state.units.append(unit_state)

    if len(state.units) > 1:
        state.pool = ThreadPoolExecutor(max_workers=len(state.units), thread_name_prefix="tid-ssd-unit")

    if profile.psu.enabled:
        state.psu = PsuReader.discover(ctx.env.api_base, profile.psu.capability, timeout_s=profile.psu.timeout_s)
        if state.psu is None:
            info("no psu capability on this bench — supply current will not be recorded")

    _write_units_artifact(ctx, state)
    ctx.extras["state"] = state


def _connect_unit(
    unit_state: _UnitState,
    unit: Unit,
    profile: TidSsdProfile,
    anomalies: AnomalyLog,
    ctx: SuiteContext,
) -> None:
    """Open the session for one unit and prepare its disks."""
    target = _ssh_target(profile, unit.host or ctx.target)
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

    if profile.dmesg.enabled:
        with contextlib.suppress(RemoteError, TimeoutError, OSError):
            _, unit_state.dmesg_cursor = probe_engine.read_kernel_log(
                client, profile.dmesg, since_cursor="", timeout=profile.probe.ssh_timeout_s
            )

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
    profile: TidSsdProfile,
    anomalies: AnomalyLog,
    iteration: int,
) -> dict[str, Any]:
    """Probe every device on one unit. Runs on a worker thread when fanned out."""
    result: dict[str, Any] = {"probe_ok": True}
    for device in profile.probe.devices:
        device_state = unit.devices[device.name]
        try:
            if profile.driver == "mock":
                data = mock.probe(iteration, device, unit.name)
            else:
                data = probe_engine.run_probe(unit.client, profile.probe, device, unit.installed_path)
        except (RemoteError, TimeoutError, OSError) as exc:
            unit.probe_errors += 1
            result["probe_ok"] = False
            result[device.name] = {"error": f"{type(exc).__name__}: {exc}"}
            flag(
                anomalies,
                "ssh",
                "probe_error",
                iteration=iteration,
                message=f"{unit.name}/{device.name}: the probe did not come back ({exc}) — this tick has no "
                "reading, and the unit may have gone with the part",
                detail={"unit": unit.name, "device": device.name, "error": str(exc)},
            )
            continue

        for anomaly in probe_engine.evaluate(device_state, profile.probe, device, data):
            flag(
                anomalies,
                anomaly.probe,
                anomaly.kind,
                iteration=iteration,
                message=f"{unit.name}: {anomaly.message}",
                detail={"unit": unit.name, **anomaly.detail},
            )

        if data.get("error"):
            result["probe_ok"] = False
        result[device.name] = {
            "device_present": data.get("device_present"),
            "write_mbps": data.get("write_mbps"),
            "read_mbps": data.get("read_mbps"),
            "verify_ok": data.get("verify_ok"),
            "verify_failures": device_state.verify_failures,
            "missing_ticks": device_state.missing_ticks,
            **{f"smart_{k}": v for k, v in device_state.last_smart.items() if isinstance(v, (int, float))},
        }

    _read_kernel_log(unit, profile, anomalies, iteration)
    return result


def _read_kernel_log(
    unit: _UnitState,
    profile: TidSsdProfile,
    anomalies: AnomalyLog,
    iteration: int,
) -> None:
    """Record the storage-related kernel messages since the last tick."""
    if not profile.dmesg.enabled:
        return
    if profile.driver == "mock":
        lines = [line for d in profile.probe.devices for line in mock.kernel_log(iteration, d)]
    else:
        try:
            lines, unit.dmesg_cursor = probe_engine.read_kernel_log(
                unit.client, profile.dmesg, since_cursor=unit.dmesg_cursor, timeout=profile.probe.ssh_timeout_s
            )
        except (RemoteError, TimeoutError, OSError):
            return
    for line in lines:
        flag(
            anomalies,
            "kernel",
            "message",
            iteration=iteration,
            message=f"{unit.name}: the kernel logged {line.strip()!r} — the driver saw something the SMART "
            "counters may not report for several ticks yet",
            detail={"unit": unit.name, "message": line.strip()},
        )


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: TidSsdProfile = ctx.profile
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
    if len(failed) < len(per_unit):
        state.measured_ticks += 1

    metrics: dict[str, Any] = {"anomalies_total": state.anomalies.total()}
    if len(state.units) == 1:
        # A single-unit run keeps the flat metric shape.
        metrics.update({k: v for k, v in next(iter(per_unit.values())).items() if k != "probe_ok"})
    else:
        metrics["units"] = per_unit
        metrics["units_ok"] = len(per_unit) - len(failed)

    if state.psu is not None:
        reading = state.psu.read()
        if reading:
            metrics["psu"] = reading

    if state.first_anomaly_iteration is None and state.anomalies.total() > 0:
        state.first_anomaly_iteration = ictx.iteration

    # The tick reports what it found, and the session carries on either way:
    # a part that has started to fail is the measurement, not the end of it.
    return IterationOutcome(
        success=not failed,
        reason=f"probe failed on {', '.join(failed)}" if failed else "",
        metrics=metrics,
        summary=_tick_summary(profile, state),
    )


def _tick_summary(profile: TidSsdProfile, state: _State) -> str:
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


def _device_metrics(metrics: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The per-device records in one tick, whichever shape the run has.

    A single-unit run keeps them at the top level; a fanned-out one nests them
    one unit deep. Keys come back as ``device`` or ``unit/device`` so a failure
    says which.
    """
    units = metrics.get("units")
    if isinstance(units, dict):
        return {
            f"{unit}/{device}": record
            for unit, per_unit in units.items()
            if isinstance(per_unit, dict)
            for device, record in per_unit.items()
            if isinstance(record, dict)
        }
    return {k: v for k, v in metrics.items() if isinstance(v, dict) and "verify_ok" in v}


def _evaluate(outcomes: list[IterationOutcome], profile: TidSsdProfile) -> tuple[bool, str] | None:
    """Whether the exposure was measured well enough to be read.

    This can only fail a run, never rescue one: the SDK has already failed it
    if any tick did. That is the campaign's shape — a part that dies fails the
    run it died in, and the whole exposure is still recorded because nothing
    aborted.
    """
    if not outcomes:
        return False, "no probe ticks completed"
    criteria = profile.pass_criteria
    last = outcomes[-1].metrics

    if criteria.require_measurement and not any(outcome.success for outcome in outcomes):
        return False, "no tick produced a bandwidth measurement"

    for key, record in _device_metrics(last).items():
        failures = record.get("verify_failures")
        if isinstance(failures, int) and failures > criteria.max_verify_failures:
            return False, f"{key}: {failures} miscompares exceeds budget of {criteria.max_verify_failures}"
        if criteria.require_device_at_end and record.get("device_present") is False:
            return False, f"{key}: the drive had left the bus when the session ended"

    anomalies = int(last.get("anomalies_total", 0))
    if anomalies > criteria.max_anomalies:
        return False, f"{anomalies} anomalies exceeds budget of {criteria.max_anomalies}"
    return True, ""


def _results(
    ctx: SuiteContext,
    _outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidSsdProfile,
) -> list[dict[str, Any]]:
    state = _state(ctx)
    multi = len(state.units) > 1
    rows = [
        make_result("ticks", "Probe ticks", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
        make_result(
            "measured_ticks",
            "Ticks that measured something",
            state.measured_ticks,
            format="int",
            highlight=profile.pass_criteria.require_measurement and state.measured_ticks == 0,
        ),
    ]
    if state.first_anomaly_iteration is not None:
        # The tick a part started to move is the figure a dose curve is read
        # against, and an average over the whole session hides it.
        rows.append(
            make_result(
                "first_anomaly_iteration",
                "First anomaly at tick",
                state.first_anomaly_iteration,
                format="int",
                highlight=True,
            )
        )
    if multi:
        rows.append(make_result("units", "Units", len(state.units), format="int"))

    for unit in state.units:
        rows += _unit_results(unit, profile, multi)

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


def _unit_results(unit: _UnitState, profile: TidSsdProfile, multi: bool) -> list[dict[str, Any]]:
    prefix = f"{unit.name}." if multi else ""
    label = f"{unit.name} " if multi else ""
    rows: list[dict[str, Any]] = []
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
            make_result(
                f"{prefix}{device.name}.missing_ticks",
                f"{label}{device.name} — ticks off the bus",
                device_state.missing_ticks,
                format="int",
                highlight=device_state.missing_ticks > 0,
            ),
        ]
        spare = device_state.last_smart.get("available_spare")
        if isinstance(spare, (int, float)):
            rows.append(
                make_result(
                    f"{prefix}{device.name}.available_spare",
                    f"{label}{device.name} — available spare",
                    int(spare),
                    unit="%",
                    format="int",
                    highlight=device_state.spare_under_threshold,
                )
            )

    rows.append(
        make_result(
            f"{prefix}alive_at_end",
            f"{label}unit alive at end".strip().capitalize(),
            "yes" if unit.alive_at_end else "no",
            highlight=not unit.alive_at_end,
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
    return rows


def _summary(ctx: SuiteContext, profile: TidSsdProfile) -> dict[str, str]:
    return {
        "devices": ", ".join(f"{d.name}={d.device}" for d in profile.probe.devices),
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "provision": "enabled" if profile.provision.enabled else "disabled",
        "sample_period_s": str(profile.sample_period_s),
        "test_size_mb": str(profile.probe.test_size_mb),
        "units": ", ".join(u.name for u in _units(ctx, profile)),
    }


def _hardware(ctx: SuiteContext, profile: TidSsdProfile) -> dict[str, dict[str, str]]:
    hardware = {u.name: {"host": u.host or ctx.target or "", "driver": profile.driver} for u in _units(ctx, profile)}
    hardware.update({d.name: {"device": d.device, "test_path": d.test_path} for d in profile.probe.devices})
    return hardware


SPEC = SuiteSpec(
    name="tid_ssd",
    profile_model=TidSsdProfile,
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
