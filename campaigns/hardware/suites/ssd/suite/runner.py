"""SSD check — does the disk on this unit write, read back and report clean.

Each tick probes every configured device over SSH: a ``dd`` write and read for
bandwidth, a SHA-256 write-verify, and the NVMe SMART counters. Half a minute
of that is enough to say whether a disk works, so the run is short and its
budgets are zero — anything anomalous is a reason to look at the disk.

Anomaly kinds: ``write_verify/miscompare``, ``smart/media_error_increased``,
``smart/error_log_increased``, ``smart/critical_warning_raised``,
``bandwidth/read_below_floor``, ``bandwidth/write_below_floor``,
``ssh/cache_drop_failed``, ``ssh/probe_error``.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_sdk import (
    AnomalyLog,
    IterationContext,
    IterationOutcome,
    RemoteTarget,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
    warn,
)
from gauntlet_sdk.remote import RemoteError, connect

from suite import probe as probe_engine
from suite.profile import SsdProfile

# The engineering key, carried as a submodule so a bench run needs nothing
# copied into ~/.ssh.
BUNDLED_KEY = Path("extras/trl-engineering-keys/saver/id_ed_saver_eng_key")


@dataclass
class _State:
    """State held across ticks."""

    anomalies: AnomalyLog
    devices: dict[str, probe_engine.DeviceState] = field(default_factory=dict)
    client: Any = None
    probe_errors: int = 0


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


def _ssh_target(profile: SsdProfile, host: str | None) -> RemoteTarget:
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


def _setup(ctx: SuiteContext) -> None:
    profile: SsdProfile = ctx.profile
    state = _State(
        anomalies=AnomalyLog(ctx.jsonl),
        devices={d.name: probe_engine.DeviceState() for d in profile.probe.devices},
    )

    if profile.driver == "mock":
        info("driver=mock — probe results are synthesised")
    else:
        target = _ssh_target(profile, ctx.target)
        info(f"connecting to {target.user}@{target.host}")
        state.client = connect(target)
        baselines = probe_engine.read_smart_baseline(
            state.client, profile.probe.devices, timeout=profile.probe.ssh_timeout_s
        )
        for name, counters in baselines.items():
            state.devices[name].smart_baseline = counters
            if not counters:
                warn(f"{name}: no SMART counters available; write-verify still applies")

    ctx.extras["state"] = state


def _teardown(ctx: SuiteContext) -> None:
    state = ctx.extras.get("state")
    if isinstance(state, _State) and state.client is not None:
        with contextlib.suppress(Exception):
            state.client.close()


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    profile: SsdProfile = ctx.profile
    state = _state(ctx)
    metrics: dict[str, Any] = {}
    failed: list[str] = []

    for device in profile.probe.devices:
        device_state = state.devices[device.name]
        try:
            if profile.driver == "mock":
                data = probe_engine.synth_probe(device)
            else:
                data = probe_engine.run_probe(state.client, profile.probe, device)
        except (RemoteError, TimeoutError, OSError) as exc:
            state.probe_errors += 1
            failed.append(device.name)
            state.anomalies.record(
                "ssh",
                "probe_error",
                iteration=ictx.iteration,
                detail={"device": device.name, "error": str(exc)},
            )
            continue

        for anomaly in probe_engine.evaluate(device_state, profile.probe, device, data):
            state.anomalies.record(anomaly.probe, anomaly.kind, iteration=ictx.iteration, detail=anomaly.detail)

        if data.get("error"):
            failed.append(device.name)
        metrics[device.name] = {
            "write_mbps": data.get("write_mbps"),
            "read_mbps": data.get("read_mbps"),
            "verify_ok": data.get("verify_ok"),
            "verify_failures": device_state.verify_failures,
            **{f"smart_{k}": v for k, v in device_state.last_smart.items() if isinstance(v, (int, float))},
        }

    metrics["anomalies_total"] = state.anomalies.total()
    return IterationOutcome(
        success=not failed,
        reason=f"probe failed on {', '.join(failed)}" if failed else "",
        metrics=metrics,
        summary=_tick_summary(profile, state),
    )


def _tick_summary(profile: SsdProfile, state: _State) -> str:
    parts = [
        f"{d.name} w={state.devices[d.name].mean_write():.0f} r={state.devices[d.name].mean_read():.0f}"
        for d in profile.probe.devices
        if state.devices[d.name].write_samples or state.devices[d.name].read_samples
    ]
    return " ".join(parts) + " MB/s" if parts else ""


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
    rows = [
        make_result("ticks", "Probe ticks", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]

    for device in profile.probe.devices:
        device_state = state.devices[device.name]
        rows += [
            make_result(
                f"{device.name}.write_mbps",
                f"{device.name} — mean write",
                round(device_state.mean_write(), 1),
                unit="MB/s",
                format="decimal",
                precision=1,
            ),
            make_result(
                f"{device.name}.read_mbps",
                f"{device.name} — mean read",
                round(device_state.mean_read(), 1),
                unit="MB/s",
                format="decimal",
                precision=1,
            ),
            make_result(
                f"{device.name}.verify_failures",
                f"{device.name} — miscompares",
                device_state.verify_failures,
                format="int",
                highlight=device_state.verify_failures > 0,
            ),
        ]

    if state.probe_errors:
        rows.append(make_result("probe_errors", "Probe errors", state.probe_errors, format="int", highlight=True))

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


def _summary(_ctx: SuiteContext, profile: SsdProfile) -> dict[str, str]:
    return {
        "devices": ", ".join(f"{d.name}={d.device}" for d in profile.probe.devices),
        "driver": profile.driver,
        "duration_s": str(profile.duration_s),
        "sample_period_s": str(profile.sample_period_s),
        "test_size_mb": str(profile.probe.test_size_mb),
    }


def _hardware(ctx: SuiteContext, profile: SsdProfile) -> dict[str, dict[str, str]]:
    hardware = {"uut": {"driver": profile.driver, "host": ctx.target or ""}}
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
