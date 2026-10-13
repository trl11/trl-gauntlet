"""Hold the run open while Gauntlet records the DAQs.

Gauntlet records every DAQ the run form ticked, at the rate it scans, and
watches the limits set there. The suite drives no instrument: it only keeps the
run going for its duration, or until the operator stops it, and says so once a
second.
"""

from __future__ import annotations

from gauntlet_sdk import IterationContext, IterationOutcome, SuiteContext, SuiteSpec

from suite.profile import DaqRecordProfile

# How often the suite reports that it is still recording.
_HEARTBEAT_S = 1.0


def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    """One heartbeat: the time recorded so far."""
    return IterationOutcome(
        success=True,
        reason="",
        metrics={},
        phase_records=[],
        summary=f"recording, {ictx.elapsed_run_s:.0f} s",
    )


def _profile_summary(ctx: SuiteContext, profile: DaqRecordProfile) -> dict[str, str]:
    return {"duration_s": str(profile.duration_s) if profile.duration_s else "until stopped"}


SPEC = SuiteSpec(
    name="daq_record",
    profile_model=DaqRecordProfile,
    iterate=_iterate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda _p: _HEARTBEAT_S,
    profile_summary=_profile_summary,
)
