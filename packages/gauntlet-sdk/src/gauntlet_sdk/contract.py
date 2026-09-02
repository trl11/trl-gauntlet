"""The Gauntlet contract, as pydantic models.

Defines the four files that cross the boundary between a suite and Gauntlet:
the suite's declaration and the three artifacts it may write. Both packages
validate against these classes.

JSON Schema is generated from them by ``gauntlet schema <name>`` and
``GET /api/schemas/{name}``.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

CONTRACT_VERSION = 1

Artifact = Literal["events", "frames", "junit", "manifest", "metrics", "summary", "verdict"]
OverrideType = Literal["boolean", "integer", "number", "string"]
ResultFormat = Literal["bytes", "decimal", "duration", "int", "percent", "text"]
TestOutcome = Literal["error", "fail", "pass", "skip"]
StopSignal = Literal["SIGUSR1", "SIGINT", "SIGTERM", "NONE"]


# ---------------------------------------------------------------------------
# suite.yaml — how a suite declares itself
# ---------------------------------------------------------------------------


class ExecSpec(BaseModel):
    """How to launch the suite process."""

    model_config = ConfigDict(extra="forbid")

    command: list[str] = Field(min_length=1, description="Base argv. Not run through a shell.")
    workdir: str = Field(default=".", description="Working directory, relative to the suite directory.")
    args: dict[str, str] = Field(
        default_factory=dict,
        description="Maps a contract value (profile, run_dir, run_id, target, unit_serial) to the flag carrying it.",
    )
    env: dict[str, str] = Field(default_factory=dict, description="Extra environment variables.")
    graceful_stop_signal: StopSignal = Field(
        default="SIGUSR1",
        description="Signal for a graceful stop. NONE forces a hard abort.",
    )
    profile_schema_command: list[str] = Field(
        default_factory=list,
        description=("Optional argv printing the JSON Schema of this suite's profile to stdout."),
    )


class OverrideSpec(BaseModel):
    """One per-run knob an operator may set."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    flag: str = Field(pattern=r"^--[a-z0-9-]+$")
    type: OverrideType = "string"
    label: str = Field(default="", description="Form label. Falls back to the name.")
    unit: str = Field(default="", description="Suffix shown after the input.")
    default: Any = None
    choices: list[str] = Field(default_factory=list, description="Renders a select instead of a text input.")
    help: str = ""
    minimum: float | None = Field(default=None, description="Lowest accepted value, for a number or an integer.")
    maximum: float | None = Field(default=None, description="Highest accepted value, for a number or an integer.")


def _default_produces() -> list[Artifact]:
    """Minimum declaration: every suite writes a verdict."""
    return ["verdict"]


class SupportsSpec(BaseModel):
    """Optional run inputs the suite accepts."""

    model_config = ConfigDict(extra="forbid")

    target: bool = True
    unit_serial: bool = False


class SuiteManifest(BaseModel):
    """A ``suite.yaml``. The entire registration surface for a suite."""

    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal[1]
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    title: str = Field(min_length=1, max_length=80)
    category: str = Field(default="general", max_length=40)
    description: str = Field(default="", max_length=500)
    setup: str = Field(
        default="",
        max_length=4000,
        description=(
            "How the bench is put together, shown under the description. Line breaks and "
            "indentation are preserved, so an ASCII diagram survives."
        ),
    )
    exec: ExecSpec
    profiles: str = "./profiles"
    conformance_profile: str = Field(
        default="",
        description="Profile `gauntlet verify --run` executes. Should need no hardware and finish in seconds.",
    )
    produces: list[Artifact] = Field(
        default_factory=_default_produces,
        description="Artifacts this suite writes. Drives which views Gauntlet offers.",
    )
    requires: list[str] = Field(
        default_factory=list,
        description="Capabilities Gauntlet must grant. A run is refused when one cannot be satisfied.",
    )
    supports: SupportsSpec = Field(default_factory=SupportsSpec)
    overrides: list[OverrideSpec] = Field(default_factory=list)
    default_metrics: list[str] = Field(
        default_factory=list,
        description=(
            "Metric series names charted and columned by default on a run's Metrics and "
            "Iterations tabs, before the operator picks their own. Falls back to the first "
            "few series reported when empty."
        ),
    )

    def override(self, name: str) -> OverrideSpec | None:
        """Look up a declared override by name."""
        return next((o for o in self.overrides if o.name == name), None)


# ---------------------------------------------------------------------------
# verdict.json — the one required artifact
# ---------------------------------------------------------------------------


class ResultRow(BaseModel):
    """A headline figure for the run summary."""

    model_config = ConfigDict(extra="allow")

    key: str
    label: str
    value: Any
    unit: str = ""
    format: ResultFormat = "text"
    precision: int | None = Field(default=None, ge=0, le=12)
    highlight: bool = False


class TestRow(BaseModel):
    """One per-test result."""

    model_config = ConfigDict(extra="allow")

    name: str
    outcome: TestOutcome
    classname: str = ""
    duration_s: float | None = Field(default=None, ge=0)
    message: str = ""
    traceback: str = ""


class Verdict(BaseModel):
    """``verdict.json``.

    Gauntlet records a run with no verdict file as an error rather than a
    failure.
    """

    model_config = ConfigDict(extra="allow")

    passed: bool
    reason: str = Field(default="", description="Why the run failed. Required when passed is false.")
    total_iterations: int = Field(default=0, ge=0)
    successes: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    duration_s: float = Field(default=0.0, ge=0)
    started_at_utc: str = ""
    ended_at_utc: str = ""
    aborted: bool = False
    abort_reason: str = ""
    stopped_early: bool = False
    results: list[ResultRow] = Field(default_factory=list)
    tests: list[TestRow] = Field(default_factory=list)

    def problems(self) -> list[str]:
        """Contract violations that validation alone cannot express."""
        issues: list[str] = []
        if not self.passed and not self.reason.strip():
            issues.append("verdict.json: `reason` is required when `passed` is false")
        return issues


# ---------------------------------------------------------------------------
# metrics.jsonl — one record per line
# ---------------------------------------------------------------------------


class MetricsRecord(BaseModel):
    """One line of ``metrics.jsonl``.

    ``kind`` selects the record type. ``iteration`` advances the run counters;
    ``live`` and ``anomaly`` do not.
    """

    model_config = ConfigDict(extra="allow")

    kind: Literal["anomaly", "iteration", "live"] = "iteration"
    timestamp: float
    iteration: int | None = Field(default=None, ge=1)
    elapsed_run_s: float | None = None
    success: bool | None = None
    reason: str = ""
    metrics: dict[str, Any] = Field(default_factory=dict)
    phases: list[PhaseEntry] = Field(default_factory=list)
    probe: str = ""
    anomaly_kind: str = ""
    detail: Any = None

    def problems(self) -> list[str]:
        """Required fields for this record kind."""
        issues: list[str] = []
        if self.kind == "iteration":
            if self.iteration is None:
                issues.append("metrics.jsonl: `iteration` is required when kind is 'iteration'")
            if self.success is None:
                issues.append("metrics.jsonl: `success` is required when kind is 'iteration'")
        elif self.kind == "anomaly" and not self.probe:
            issues.append("metrics.jsonl: `probe` is required when kind is 'anomaly'")
        return issues


class PhaseEntry(BaseModel):
    """A named step inside one iteration."""

    model_config = ConfigDict(extra="allow")

    name: str
    elapsed_s: float = Field(ge=0)
    success: bool
    error: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# manifest.json — provenance
# ---------------------------------------------------------------------------


class RunManifest(BaseModel):
    """``manifest.json``. Provenance for one run."""

    model_config = ConfigDict(extra="allow")

    suite: str
    run_id: str
    started_at_utc: str
    hostname: str = ""
    platform: str = ""
    python_version: str = ""
    cwd: str = ""
    command_line: list[str] = Field(default_factory=list)
    repo_sha: str | None = None
    repo_branch: str | None = None
    repo_dirty: bool = False
    target: str | None = None
    unit_serial: str | None = None
    profile_path: str | None = None
    profile_summary: dict[str, str] = Field(default_factory=dict)
    hardware: dict[str, dict[str, str]] = Field(default_factory=dict)
    versions: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


MetricsRecord.model_rebuild()


CONTRACT_MODELS: dict[str, type[BaseModel]] = {
    "manifest": RunManifest,
    "metrics-record": MetricsRecord,
    "suite": SuiteManifest,
    "verdict": Verdict,
}


def json_schema(name: str) -> dict[str, Any]:
    """Generate the JSON Schema for one contract model."""
    try:
        model = CONTRACT_MODELS[name]
    except KeyError:
        known = ", ".join(sorted(CONTRACT_MODELS))
        raise LookupError(f"unknown contract model {name!r} (known: {known})") from None
    return model.model_json_schema()
