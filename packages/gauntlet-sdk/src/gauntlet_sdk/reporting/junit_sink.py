"""JUnit XML sink — one testcase per iteration, for CI consumers."""

from __future__ import annotations

import time
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path

from gauntlet_sdk.iteration import IterationContext, IterationOutcome, RunResult


class JUnitSink:
    """Buffers iterations in memory and writes the XML at end of run.

    Register :meth:`finalize` as an end-of-run callback, or use :meth:`bind`
    to get it as a plain callable.
    """

    def __init__(self, path: Path, suite_name: str) -> None:
        self._path = path
        self._suite_name = suite_name
        self._records: list[tuple[IterationOutcome, IterationContext]] = []
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def __call__(self, outcome: IterationOutcome, ctx: IterationContext) -> None:
        self._records.append((outcome, ctx))

    def bind(self) -> Callable[[RunResult], None]:
        """Return :meth:`finalize` as an end-of-run callback."""
        return self.finalize

    def finalize(self, result: RunResult) -> None:
        """Write the JUnit document."""
        suite = ET.Element(
            "testsuite",
            attrib={
                "name": self._suite_name,
                "tests": str(result.total_iterations),
                "failures": str(result.failures),
                "errors": "0",
                "time": f"{result.duration_s:.3f}",
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(result.started_at)),
            },
        )
        for outcome, ctx in self._records:
            case = ET.SubElement(
                suite,
                "testcase",
                attrib={
                    "classname": self._suite_name,
                    "name": f"iteration-{ctx.iteration}",
                    "time": f"{sum(p.elapsed_s for p in outcome.phase_records):.3f}",
                },
            )
            if not outcome.success:
                failure = ET.SubElement(
                    case,
                    "failure",
                    attrib={"message": outcome.reason or "iteration failed"},
                )
                failure.text = _render_phases(outcome)
        ET.ElementTree(suite).write(self._path, encoding="utf-8", xml_declaration=True)


def _render_phases(outcome: IterationOutcome) -> str:
    if not outcome.phase_records:
        return outcome.reason or ""
    lines = [f"reason: {outcome.reason}", "phases:"]
    for phase in outcome.phase_records:
        line = f"  {phase.name}: {phase.elapsed_s:.2f}s [{'ok' if phase.success else 'FAIL'}]"
        if phase.error:
            line += f" - {phase.error}"
        lines.append(line)
    return "\n".join(lines)
