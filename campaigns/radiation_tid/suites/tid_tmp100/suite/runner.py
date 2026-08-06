"""TMP100 Temperature Sensor.

Placeholder. The measurement is not implemented yet: this suite runs and
passes without hardware, so the campaign has a green baseline to fill in.

Component      TMP100AQDBVRQ1
Test vehicle   PRE-10161
Host           Raspberry Pi
Fixture        1-1

To implement:
  - Read temperature from the chamber and compare it against a thermocouple.

Hardware       Waiting on PRE-10161. A thermocouple is to be added to the BOM.
Test plan edits outstanding:
  - Add a note indicating what the orange block is.
  - Update the drawing to show a Raspberry Pi rather than a USB-I2C dongle.
"""

from __future__ import annotations

from gauntlet_sdk import (
    IterationContext,
    IterationOutcome,
    PhaseTimer,
    RunResult,
    SuiteContext,
    SuiteSpec,
    make_result,
)
from pydantic import BaseModel, ConfigDict, Field


class TidTmp100Profile(BaseModel):
    """What an operator can configure.

    Every field becomes a form control in the UI; ``description`` is its label.
    """

    model_config = ConfigDict(extra="forbid")

    description: str = Field(default="", description="Shown in the profile picker.")
    duration_s: float = Field(default=60.0, gt=0, description="How long to run.")
    sample_period_s: float = Field(default=1.0, gt=0, description="Seconds between samples.")


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One tick of work.

    Return ``success=False`` with a reason to fail this iteration. Numeric
    values in ``metrics`` are plotted. ``ctx.target`` is the unit under test;
    ``ctx.artifact("frames", "x.jpg")`` resolves a path in the run directory.
    """
    with PhaseTimer("measure", phases := []) as phase:
        phase.set_detail(target=ctx.target or "local")
        # TODO: replace with a real measurement.
        value = 1.0

    return IterationOutcome(
        success=True,
        metrics={"value": value},
        phase_records=phases,
        summary=f"{value:.2f}",
    )


def _evaluate(outcomes: list[IterationOutcome], profile: TidTmp100Profile) -> tuple[bool, str] | None:
    """Aggregate pass criteria, checked once with every outcome.

    Return ``None`` to accept the default: pass if no iteration failed.
    """
    if not outcomes:
        return False, "no samples collected"
    return None


def _results(
    ctx: SuiteContext,
    outcomes: list[IterationOutcome],
    result: RunResult,
    profile: TidTmp100Profile,
) -> list[dict]:
    """Headline figures shown at the top of the run summary."""
    return [
        make_result("samples", "Samples", result.total_iterations, format="int"),
        make_result("duration", "Duration", round(result.duration_s, 1), format="duration"),
    ]


SPEC = SuiteSpec(
    name="tid_tmp100",
    profile_model=TidTmp100Profile,
    iterate=_iterate,
    evaluate=_evaluate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
    verdict_results=_results,
)
