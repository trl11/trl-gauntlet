"""Renders ``summary.md`` from the artifacts in a run directory."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_summary(run_dir: Path, *, suite_name: str = "") -> Path | None:
    """Write ``summary.md`` beside the other artifacts.

    Returns the path, or ``None`` when there is no verdict to summarize.
    """
    verdict = _read_json(run_dir / "verdict.json")
    if verdict is None:
        return None
    manifest = _read_json(run_dir / "manifest.json") or {}

    suite = suite_name or str(manifest.get("suite") or run_dir.parent.name)
    passed = bool(verdict.get("passed"))
    lines = [
        f"# {suite} — {'PASS' if passed else 'FAIL'}",
        "",
    ]
    if not passed and verdict.get("reason"):
        lines += [f"**Reason:** {verdict['reason']}", ""]

    rows = [
        ("Run id", manifest.get("run_id") or verdict.get("run_id")),
        ("Started", verdict.get("started_at_utc") or manifest.get("started_at_utc")),
        ("Duration", _duration(verdict.get("duration_s"))),
        ("Target", manifest.get("target")),
        ("Unit serial", manifest.get("unit_serial")),
        ("Profile", manifest.get("profile_path")),
        ("Iterations", _counts(verdict)),
    ]
    present = [(label, value) for label, value in rows if value not in (None, "")]
    if present:
        lines += ["| Field | Value |", "|---|---|"]
        lines += [f"| {label} | {value} |" for label, value in present]
        lines.append("")

    results = verdict.get("results")
    if isinstance(results, list) and results:
        lines += ["## Results", "", "| Metric | Value |", "|---|---|"]
        for entry in results:
            if not isinstance(entry, dict):
                continue
            unit = f" {entry['unit']}" if entry.get("unit") else ""
            lines.append(f"| {entry.get('label', entry.get('key', '?'))} | {entry.get('value')}{unit} |")
        lines.append("")

    summary = verdict.get("profile_summary") or manifest.get("profile_summary")
    if isinstance(summary, dict) and summary:
        lines += ["## Profile", "", "| Field | Value |", "|---|---|"]
        lines += [f"| {k} | {v} |" for k, v in sorted(summary.items())]
        lines.append("")

    path = run_dir / "summary.md"
    path.write_text("\n".join(lines))
    return path


def _counts(verdict: dict[str, Any]) -> str | None:
    total = verdict.get("total_iterations")
    if not isinstance(total, int):
        return None
    return f"{verdict.get('successes', 0)} ok / {verdict.get('failures', 0)} failed of {total}"


def _duration(seconds: Any) -> str | None:
    if not isinstance(seconds, (int, float)):
        return None
    if seconds < 60:
        return f"{seconds:.1f}s"
    if seconds < 3600:
        return f"{seconds / 60:.1f}m"
    return f"{seconds / 3600:.2f}h"


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None
