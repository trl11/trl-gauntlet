"""Sampled Linux system-statistics suite.

Each tick reads every statistic the host offers, checks the reading against the
thresholds in the profile, records an anomaly per failing check, and writes one
metrics record. The run rolls up into a verdict with a headline figure per
statistic and a test row per check.

Anomaly kinds: ``cpu/utilisation_above_ceiling``, ``disk/free_space_below_floor``,
``load/load_above_ceiling``, ``memory/available_below_floor``,
``network/counters_increased``, ``thermal/temperature_above_ceiling``.
"""

from __future__ import annotations

import os
import platform
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from gauntlet_sdk import (
    AnomalyLog,
    IterationContext,
    IterationOutcome,
    PhaseRecord,
    PhaseTimer,
    RunResult,
    SuiteContext,
    SuiteSpec,
    info,
    make_result,
    make_test,
)
from gauntlet_sdk.reporting.verdict import ResultFormat, TestOutcome

from suite.checks import CheckResult, run_checks
from suite.host import read_cpu_model
from suite.memory import read_meminfo
from suite.metrics import to_metrics
from suite.profile import SystemStatsProfile
from suite.sampler import Sample, Sampler


@dataclass
class _Tally:
    """Running counts for one check across the run."""

    failures: int = 0
    passes: int = 0
    reason: str = ""
    skip_reason: str = ""
    skips: int = 0

    @property
    def outcome(self) -> TestOutcome:
        if self.failures:
            return "fail"
        return "pass" if self.passes else "skip"


@dataclass
class _State:
    """What the run accumulates across ticks."""

    anomalies: AnomalyLog
    sampler: Sampler
    available_memory_percent: list[float] = field(default_factory=list)
    context_switches_per_s: list[float] = field(default_factory=list)
    cpu_percent: list[float] = field(default_factory=list)
    free_disk_percent: list[float] = field(default_factory=list)
    host: dict[str, str] = field(default_factory=dict)
    load_per_core: list[float] = field(default_factory=list)
    tallies: dict[str, _Tally] = field(default_factory=dict)
    temperature_c: list[float] = field(default_factory=list)

    def tally(self, result: CheckResult) -> _Tally:
        return self.tallies.setdefault(result.name, _Tally())


def _setup(ctx: SuiteContext) -> None:
    """Describe the host, then take the read the first delta is measured from."""
    profile: SystemStatsProfile = ctx.profile
    memory = read_meminfo()
    host = {
        "arch": platform.machine(),
        "cpu_count": str(os.cpu_count() or 0),
        "cpu_model": read_cpu_model() or "unknown",
        "hostname": platform.node(),
        "kernel": platform.release(),
        "memory_total_bytes": str(memory.total_bytes if memory is not None else 0),
        "os": platform.system(),
    }
    info(f"host {host['hostname']} — {host['cpu_model']} x{host['cpu_count']}, kernel {host['kernel']}")

    sampler = Sampler(min_window_s=min(0.1, profile.sample_period_s))
    sampler.prime()
    ctx.extras["state"] = _State(anomalies=AnomalyLog(ctx.jsonl), host=host, sampler=sampler)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """Read every statistic once and check it against the profile."""
    state = _state(ctx)
    profile: SystemStatsProfile = ctx.profile
    phases: list[PhaseRecord] = []

    with PhaseTimer("sample", phases) as phase:
        sample = state.sampler.sample()
        phase.set_detail(
            cores=sample.cpu_count,
            disks=len(sample.disks),
            interfaces=len(sample.network),
            zones=len(sample.thermal),
        )

    with PhaseTimer("check", phases) as phase:
        results = run_checks(sample, profile)
        failures = [result for result in results if result.failed]
        phase.set_detail(checked=len(results), failed=len(failures))

    for result in results:
        tally = state.tally(result)
        if result.failed:
            tally.failures += 1
            tally.reason = tally.reason or result.reason
            state.anomalies.record(result.name, result.kind, iteration=ictx.iteration, detail=result.detail)
        elif result.status == "skip":
            tally.skips += 1
            tally.skip_reason = tally.skip_reason or result.reason
        else:
            tally.passes += 1
    _accumulate(state, sample)

    return IterationOutcome(
        success=not failures,
        reason="; ".join(result.reason for result in failures),
        metrics=to_metrics(sample),
        phase_records=phases,
        summary=_summary(sample),
    )


def _evaluate(outcomes: list[IterationOutcome], profile: SystemStatsProfile) -> tuple[bool, str] | None:
    """A run that collected nothing proves nothing about the host."""
    if not outcomes:
        return False, "no samples collected"
    return True, ""


def _hardware(ctx: SuiteContext, profile: SystemStatsProfile) -> dict[str, dict[str, str]]:
    """Host facts for the run manifest."""
    return {"host": dict(_state(ctx).host)}


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: SystemStatsProfile,
) -> list[dict[str, Any]]:
    """Headline figures for the run summary."""
    state = _state(ctx)
    rows = [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("failed_samples", "Failed samples", result.failures, format="int", highlight=result.failures > 0),
        make_result("anomalies", "Anomalies", state.anomalies.total(), format="int"),
    ]
    rows += _series("cpu_peak", "Peak CPU", state.cpu_percent, max, "percent")
    rows += _series("cpu_mean", "Mean CPU", state.cpu_percent, _mean, "percent")
    rows += _series("memory_available_min", "Lowest available memory", state.available_memory_percent, min, "percent")
    rows += _series("disk_free_min", "Lowest free disk", state.free_disk_percent, min, "percent")
    rows += _series("load_peak", "Peak load per core", state.load_per_core, max, "decimal")
    rows += _series("temperature_peak", "Peak temperature", state.temperature_c, max, "decimal", unit="C")
    rows += _series(
        "context_switches_mean",
        "Mean context switches",
        state.context_switches_per_s,
        _mean,
        "decimal",
        unit="/s",
    )
    rows.append(make_result("duration", "Duration", round(result.duration_s, 1), format="duration"))
    return rows


def _tests(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    profile: SystemStatsProfile,
) -> list[dict[str, Any]]:
    """One row per check, so the verdict names which statistic went out of range."""
    state = _state(ctx)
    rows: list[dict[str, Any]] = []
    for name in sorted(state.tallies):
        tally = state.tallies[name]
        outcome = tally.outcome
        if outcome == "fail":
            message = f"{tally.failures}/{tally.failures + tally.passes} samples failed: {tally.reason}"
        elif outcome == "skip":
            message = tally.skip_reason or "not measured"
        else:
            message = f"{tally.passes} samples within limits"
        rows.append(make_test(name, classname="system_stats", message=message, outcome=outcome))
    return rows


def _versions(ctx: SuiteContext, profile: SystemStatsProfile) -> dict[str, str]:
    return {"python": platform.python_version()}


def _accumulate(state: _State, sample: Sample) -> None:
    """Fold one sample into the series the verdict reports."""
    if sample.cpu is not None:
        state.cpu_percent.append(sample.cpu.overall_percent)
    if sample.context_switches_per_s is not None:
        state.context_switches_per_s.append(sample.context_switches_per_s)
    if sample.memory is not None:
        state.available_memory_percent.append(sample.memory.available_percent)
    tightest = sample.tightest_disk
    if tightest is not None:
        state.free_disk_percent.append(tightest.free_percent)
    if sample.load_per_core is not None:
        state.load_per_core.append(sample.load_per_core)
    hottest = sample.hottest
    if hottest is not None:
        state.temperature_c.append(hottest.celsius)


def _mean(values: list[float]) -> float:
    return sum(values) / len(values)


def _series(
    key: str,
    label: str,
    values: list[float],
    reduce: Callable[[list[float]], float],
    fmt: ResultFormat,
    *,
    unit: str | None = None,
) -> list[dict[str, Any]]:
    """One result row, or none at all when the host never offered the reading."""
    if not values:
        return []
    return [make_result(key, label, round(float(reduce(values)), 2), format=fmt, precision=2, unit=unit)]


def _state(ctx: SuiteContext) -> _State:
    state = ctx.extras.get("state")
    if not isinstance(state, _State):
        raise RuntimeError("setup did not populate ctx.extras['state']")
    return state


def _summary(sample: Sample) -> str:
    """The one-line tail of the iteration's log entry."""
    parts: list[str] = []
    if sample.cpu is not None:
        parts.append(f"cpu {sample.cpu.overall_percent:.0f}%")
    if sample.memory is not None:
        parts.append(f"mem {sample.memory.available_percent:.0f}% free")
    if sample.load_per_core is not None:
        parts.append(f"load {sample.load_per_core:.2f}/core")
    tightest = sample.tightest_disk
    if tightest is not None:
        parts.append(f"disk {tightest.free_percent:.0f}% free")
    hottest = sample.hottest
    if hottest is not None:
        parts.append(f"{hottest.celsius:.0f}C")
    return "  ".join(parts)


SPEC = SuiteSpec(
    name="system_stats",
    profile_model=SystemStatsProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    stop_on_failure=lambda p: p.stop_on_failure,
    setup=_setup,
    hardware_summary=_hardware,
    versions=_versions,
    verdict_results=_results,
    verdict_tests=_tests,
)
