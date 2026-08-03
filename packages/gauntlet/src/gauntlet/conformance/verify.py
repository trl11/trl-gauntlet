"""Checks a suite against the contract.

The static pass validates ``suite.yaml`` and everything checkable without
execution. The ``--run`` pass executes the suite's conformance profile into a
temporary directory and validates the artifacts it produced.

Results are :class:`Check` records, rendered by both the CLI and the API.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet_suite.contract import MetricsRecord, RunManifest, Verdict
from gauntlet_suite.environment import new_run_id
from pydantic import ValidationError

from gauntlet.suites.discovery import list_profiles
from gauntlet.suites.manifest import LoadedSuite, ManifestError, load_suite
from gauntlet.supervisor.launcher import RunRequest, build_launch

# Artifacts a suite may declare, and the file each one means.
_ARTIFACT_FILES = {
    "events": "events.sqlite",
    "junit": "junit.xml",
    "manifest": "manifest.json",
    "metrics": "metrics.jsonl",
    "summary": "summary.md",
    "verdict": "verdict.json",
}

_MAX_METRICS_LINES_CHECKED = 500


@dataclass
class Check:
    """One conformance finding."""

    name: str
    passed: bool
    detail: str = ""
    fatal: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail, "fatal": self.fatal}


@dataclass
class Report:
    """The outcome of verifying one suite."""

    suite: str
    directory: str
    checks: list[Check] = field(default_factory=list)
    executed: bool = False
    run_dir: str = ""

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)

    def add(self, name: str, passed: bool, detail: str = "", *, fatal: bool = False) -> Check:
        check = Check(name=name, passed=passed, detail=detail, fatal=fatal)
        self.checks.append(check)
        return check

    def to_dict(self) -> dict[str, Any]:
        return {
            "suite": self.suite,
            "directory": self.directory,
            "passed": self.passed,
            "executed": self.executed,
            "run_dir": self.run_dir,
            "checks": [c.to_dict() for c in self.checks],
        }


def verify_suite(directory: Path, *, execute: bool = False, timeout_s: float = 300.0) -> Report:
    """Verify the suite rooted at ``directory``.

    With ``execute``, the conformance profile runs into a temporary directory
    that is removed afterwards.
    """
    report = Report(suite=directory.name, directory=str(directory))
    try:
        suite = load_suite(directory)
    except ManifestError as exc:
        report.add("suite.yaml is valid", False, str(exc), fatal=True)
        return report

    report.suite = suite.key
    report.add("suite.yaml is valid", True, f"key={suite.key} apiVersion=1")
    _check_static(suite, report)
    if execute:
        _check_execution(suite, report, timeout_s=timeout_s)
    return report


def _check_static(suite: LoadedSuite, report: Report) -> None:
    """Everything checkable without running the suite."""
    command = suite.manifest.exec.command
    executable = _resolve_command(command[0], suite.workdir) if command else None
    report.add(
        "exec.command resolves",
        executable is not None,
        f"{command[0]} -> {executable}" if executable else f"{command[0]} is not executable or not on PATH",
    )

    report.add(
        "exec.workdir exists",
        suite.workdir.is_dir(),
        str(suite.workdir),
    )

    if "verdict" not in suite.manifest.produces:
        report.add(
            "produces includes verdict",
            False,
            "every suite must write verdict.json; add it to `produces`",
        )
    else:
        report.add("produces includes verdict", True)

    args = suite.manifest.exec.args
    report.add(
        "run_dir is passed or read from the environment",
        True,
        "via --" + args["run_dir"].lstrip("-") if "run_dir" in args else "via GAUNTLET_RUN_DIR",
    )

    profiles = list_profiles(suite)
    report.add("profiles directory has at least one profile", bool(profiles), f"{len(profiles)} found")

    conformance = suite.manifest.conformance_profile
    if conformance:
        found = any(p.name == conformance or Path(p.name).stem == conformance for p in profiles)
        report.add("conformance_profile exists", found, conformance)
    else:
        report.add(
            "conformance_profile is declared",
            False,
            "set `conformance_profile` to a hardware-free profile so `verify --run` can execute this suite",
        )


def _check_execution(suite: LoadedSuite, report: Report, *, timeout_s: float) -> None:
    """Run the conformance profile and validate what it wrote."""
    profiles = list_profiles(suite)
    wanted = suite.manifest.conformance_profile
    profile_path = next(
        (p.path for p in profiles if p.name == wanted or Path(p.name).stem == wanted),
        None,
    )
    if wanted and profile_path is None:
        report.add("run: conformance profile resolved", False, f"{wanted} not found")
        return

    with tempfile.TemporaryDirectory(prefix="gauntlet-verify-") as scratch:
        run_dir = Path(scratch) / "run"
        run_dir.mkdir(parents=True)
        report.run_dir = str(run_dir)
        launch = build_launch(
            suite,
            RunRequest(suite=suite.key),
            run_id=new_run_id(),
            run_dir=run_dir,
            profile_path=profile_path,
            api_base=None,
            capability_env=None,
        )
        try:
            completed = subprocess.run(
                launch.argv,
                cwd=str(launch.cwd),
                env={**launch.env, "GAUNTLET_VERIFY": "1"},
                capture_output=True,
                text=True,
                timeout=timeout_s,
                check=False,
            )
        except subprocess.TimeoutExpired:
            report.add("run: suite completes", False, f"timed out after {timeout_s:.0f}s", fatal=True)
            return
        except OSError as exc:
            report.add("run: suite starts", False, str(exc), fatal=True)
            return

        report.executed = True
        report.add(
            "run: suite exits cleanly",
            completed.returncode in (0, 1),
            f"exit code {completed.returncode}"
            + (f"\n{_tail(completed.stdout)}" if completed.returncode not in (0, 1) else ""),
        )
        _check_artifacts(suite, run_dir, report)


def _check_artifacts(suite: LoadedSuite, run_dir: Path, report: Report) -> None:
    """Validate what the run left behind against the contract."""
    verdict_path = run_dir / "verdict.json"
    if not verdict_path.is_file():
        report.add("run: verdict.json written", False, "a run with no verdict is recorded as an error", fatal=True)
        return
    report.add("run: verdict.json written", True)

    verdict = _validate(verdict_path, Verdict, report, "verdict.json")
    if verdict is not None:
        problems = verdict.problems()
        report.add("run: verdict.json is self-consistent", not problems, "; ".join(problems))

    for artifact in suite.manifest.produces:
        if artifact in {"verdict", "frames"}:
            continue
        filename = _ARTIFACT_FILES.get(artifact)
        if filename is None:
            continue
        exists = (run_dir / filename).exists()
        report.add(f"run: declared artifact {artifact} written", exists, filename)

    if suite.produces("frames"):
        frames = run_dir / "frames"
        report.add(
            "run: declared artifact frames written",
            frames.is_dir() and any(frames.iterdir()),
            str(frames),
        )

    if (run_dir / "manifest.json").is_file():
        _validate(run_dir / "manifest.json", RunManifest, report, "manifest.json")

    metrics = run_dir / "metrics.jsonl"
    if metrics.is_file():
        _check_metrics(metrics, report)

    undeclared = sorted(
        artifact
        for artifact, filename in _ARTIFACT_FILES.items()
        if (run_dir / filename).exists() and artifact not in suite.manifest.produces
    )
    report.add(
        "run: no undeclared artifacts",
        not undeclared,
        f"written but not in `produces`: {', '.join(undeclared)}" if undeclared else "",
    )


def _check_metrics(path: Path, report: Report) -> None:
    """Validate the first records of metrics.jsonl."""
    errors: list[str] = []
    checked = 0
    try:
        with path.open(encoding="utf-8") as handle:
            for number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                if checked >= _MAX_METRICS_LINES_CHECKED:
                    break
                checked += 1
                try:
                    record = MetricsRecord.model_validate(json.loads(line))
                except json.JSONDecodeError as exc:
                    errors.append(f"line {number}: not JSON ({exc.msg})")
                    continue
                except ValidationError as exc:
                    errors.append(f"line {number}: {_first_error(exc)}")
                    continue
                errors.extend(f"line {number}: {problem}" for problem in record.problems())
    except OSError as exc:
        report.add("run: metrics.jsonl is readable", False, str(exc))
        return
    report.add(
        "run: metrics.jsonl records match the contract",
        not errors,
        "\n".join(errors[:5]) if errors else f"{checked} records checked",
    )


def _validate(path: Path, model: type[Any], report: Report, label: str) -> Any | None:
    try:
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        report.add(f"run: {label} is valid JSON", False, str(exc))
        return None
    try:
        parsed = model.model_validate(raw)
    except ValidationError as exc:
        report.add(f"run: {label} matches the contract", False, _first_error(exc))
        return None
    report.add(f"run: {label} matches the contract", True)
    return parsed


def _resolve_command(command: str, workdir: Path) -> Path | None:
    """Locate the executable, resolving paths relative to the suite workdir."""
    if "/" in command:
        candidate = (workdir / command).resolve()
        return candidate if os.access(candidate, os.X_OK) else None
    found = shutil.which(command, path=os.pathsep.join([str(Path(sys.executable).parent), os.environ.get("PATH", "")]))
    return Path(found) if found else None


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    location = ".".join(str(part) for part in error["loc"]) or "<root>"
    return f"{location}: {error['msg']}"


def _tail(text: str, lines: int = 15) -> str:
    return os.linesep.join(text.strip().splitlines()[-lines:])
