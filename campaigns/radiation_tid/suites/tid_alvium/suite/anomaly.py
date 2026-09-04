"""Recording an anomaly and saying in the run log what it means.

Anomalies land in ``events.jsonl``, where the detail is numbers: a counter
step, a pair of byte totals, two digests that no longer match. Reading them
means knowing what the check was comparing. The run log is what an operator
watches while the beam is on, so every anomaly also says in one sentence what
has gone wrong and what it costs the measurement.

Going through :func:`flag` is what keeps the two together. Calling
``AnomalyLog.record`` directly records an anomaly that nothing announces.
"""

from __future__ import annotations

from typing import Any

from gauntlet_sdk import AnomalyLog, warn


def flag(
    anomalies: AnomalyLog,
    probe: str,
    kind: str,
    *,
    iteration: int,
    message: str,
    detail: dict[str, Any] | None = None,
) -> None:
    """Record one anomaly and warn what it means."""
    anomalies.record(probe, kind, iteration=iteration, detail=detail)
    warn(message)
