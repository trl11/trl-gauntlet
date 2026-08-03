"""Reference sampled suite.

A profile model, an ``iterate`` callable, and a :class:`SuiteSpec`. The run
directory, sinks, signal handling, verdict and summary come from
:func:`gauntlet_suite.run_suite`.
"""

from __future__ import annotations

import math
import time

from gauntlet_suite import (
    IterationContext,
    IterationOutcome,
    PhaseRecord,
    PhaseTimer,
    RunResult,
    SuiteContext,
    SuiteSpec,
    make_result,
)
from pydantic import BaseModel, ConfigDict, Field


class ExampleProfile(BaseModel):
    """What an operator can configure for this suite."""

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    duration_s: float = Field(default=10.0, gt=0, description="How long to keep sampling.")
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between samples.")
    max_temperature_c: float = Field(default=70.0, description="Fail any sample above this.")
    stop_on_failure: bool = Field(default=False, description="Abandon the run on the first failure.")


def _read_temperature(elapsed_s: float) -> float:
    """Synthetic measurement. A real suite reads hardware here."""
    return 45.0 + 12.0 * (1 - math.exp(-elapsed_s / 8.0)) + 3.0 * math.sin(elapsed_s / 2.0)


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One sample tick."""
    phases: list[PhaseRecord] = []
    with PhaseTimer("measure", phases) as phase:
        phase.set_detail(target=ctx.target or "local")
        temperature = _read_temperature(ictx.elapsed_run_s)
        time.sleep(0.01)

    limit = ctx.profile.max_temperature_c
    over = temperature > limit
    return IterationOutcome(
        success=not over,
        reason=f"temperature {temperature:.1f}C exceeds limit {limit:.1f}C" if over else "",
        metrics={"temperature_c": round(temperature, 2), "limit_c": limit},
        phase_records=phases,
        summary=f"{temperature:.1f}C",
    )


def _evaluate(outcomes: list[IterationOutcome], profile: ExampleProfile) -> tuple[bool, str] | None:
    """Aggregate check, run once with every outcome."""
    if not outcomes:
        return False, "no samples collected"
    temperatures = [o.metrics.get("temperature_c", 0.0) for o in outcomes]
    peak = max(temperatures)
    if peak > profile.max_temperature_c:
        return False, f"peak temperature {peak:.1f}C exceeds limit {profile.max_temperature_c:.1f}C"
    return True, ""


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: ExampleProfile,
) -> list[dict[str, object]]:
    """Headline figures for the run summary."""
    temperatures = [o.metrics.get("temperature_c", 0.0) for o in outcomes] or [0.0]
    return [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("peak_c", "Peak temperature", round(max(temperatures), 2), unit="C", format="decimal", precision=2),
        make_result(
            "mean_c",
            "Mean temperature",
            round(sum(temperatures) / len(temperatures), 2),
            unit="C",
            format="decimal",
            precision=2,
        ),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]


SPEC = SuiteSpec(
    name="example_sampled",
    profile_model=ExampleProfile,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    stop_on_failure=lambda p: p.stop_on_failure,
    verdict_results=_results,
)
