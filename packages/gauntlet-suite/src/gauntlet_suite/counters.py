"""Tracking a monotonic counter stream for gaps and reordering.

A link under test sends an incrementing counter; the receiver reports what
arrived. :class:`CounterTracker` folds each observed value into running totals
and reports what was anomalous about it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CounterEvent = Literal["gap", "out_of_order"]


@dataclass
class Observation:
    """What one observed counter value implied."""

    event: CounterEvent | None = None
    expected: int = 0
    observed: int = 0
    missing: int = 0

    @property
    def anomalous(self) -> bool:
        return self.event is not None


@dataclass
class CounterTracker:
    """Running state for one counter stream.

    The first value seen sets the baseline, so a receiver that joins a stream
    already in progress does not report everything before it as missing.
    """

    first_observed: int | None = None
    last_observed: int | None = None
    expected_next: int = 0
    received_total: int = 0
    missing_total: int = 0
    out_of_order_total: int = 0
    observations: list[Observation] = field(default_factory=list)

    def observe(self, value: int) -> Observation:
        """Fold one observed counter value in and describe it."""
        if self.first_observed is None:
            self.first_observed = value
            self.expected_next = value

        if value < self.expected_next:
            self.out_of_order_total += 1
            return Observation(event="out_of_order", expected=self.expected_next, observed=value)

        result = Observation(expected=self.expected_next, observed=value)
        if value > self.expected_next:
            gap = value - self.expected_next
            self.missing_total += gap
            result = Observation(event="gap", expected=self.expected_next, observed=value, missing=gap)

        self.received_total += 1
        self.last_observed = value
        self.expected_next = value + 1
        return result

    @property
    def expected_total(self) -> int:
        """How many values should have arrived across the observed range."""
        if self.first_observed is None or self.last_observed is None:
            return 0
        return self.last_observed - self.first_observed + 1

    @property
    def loss_pct(self) -> float:
        """Percentage of the observed range that never arrived."""
        expected = self.expected_total
        return round(100.0 * self.missing_total / expected, 4) if expected else 0.0
