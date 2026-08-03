"""Named steps within a single iteration.

Records per-step timing and success for iterations composed of several stages.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from types import TracebackType


@dataclass
class PhaseRecord:
    """Outcome of one phase. ``elapsed_s`` is recorded on failure as well."""

    name: str
    elapsed_s: float
    success: bool
    error: str | None = None
    detail: dict[str, str] = field(default_factory=dict)


class PhaseTimer:
    """Context manager that appends a :class:`PhaseRecord` on exit.

    Exceptions are recorded and re-raised::

        records: list[PhaseRecord] = []
        with PhaseTimer("boot", records) as phase:
            phase.set_detail(host=host)
            wait_for_boot(timeout=60)
    """

    def __init__(self, name: str, sink: list[PhaseRecord]) -> None:
        self._name = name
        self._sink = sink
        self._start = 0.0
        self._detail: dict[str, str] = {}

    def set_detail(self, **kwargs: object) -> None:
        """Attach key/value context shown alongside the phase in the timeline."""
        self._detail.update({k: str(v) for k, v in kwargs.items()})

    def __enter__(self) -> PhaseTimer:
        self._start = time.monotonic()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        success = exc_type is None
        self._sink.append(
            PhaseRecord(
                name=self._name,
                elapsed_s=time.monotonic() - self._start,
                success=success,
                error=None if success else f"{exc_type.__name__}: {exc_value}",  # type: ignore[union-attr]
                detail=self._detail,
            )
        )
