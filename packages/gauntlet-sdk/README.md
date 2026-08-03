# gauntlet-sdk

The library for writing test suites that run under [Gauntlet](../../README.md).

Requires pydantic and pyyaml. SSH support is the optional `remote` extra.

## Install

```bash
pip install gauntlet-sdk
```

## Usage

```python
from gauntlet_sdk import IterationOutcome, SuiteSpec, make_suite_cli
from pydantic import BaseModel, ConfigDict, Field


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_s: float = Field(default=60.0, description="How long to run.")
    sample_period_s: float = Field(default=1.0, description="Seconds between samples.")
    max_temperature_c: float = Field(default=70.0, description="Fail above this.")


def _iterate(ctx, ictx):
    temperature = read_sensor(ctx.target)
    over = temperature >= ctx.profile.max_temperature_c
    return IterationOutcome(
        success=not over,
        reason=f"{temperature:.1f}C exceeds limit" if over else "",
        metrics={"temperature_c": temperature},
    )


SPEC = SuiteSpec(
    name="thermal_cycle",
    profile_model=Profile,
    iterate=_iterate,
    duration_seconds=lambda p: p.duration_s,
    sample_period_seconds=lambda p: p.sample_period_s,
)

main = make_suite_cli(SPEC)
```

`run_suite` resolves the run directory, opens the sinks, handles the stop
signal, and writes every required artifact. `make_suite_cli` provides the flags
named in the contract plus `--print-profile-schema`, which Gauntlet calls to
render a profile form.

Profile fields become form controls and their `description` is the label text.
`extra="forbid"` rejects unknown keys at load time.

## Not using the loop

`SuiteSpec` covers suites that sample on a cadence. Otherwise import the
writers directly:

```python
from gauntlet_sdk import run_environment, write_simple_verdict

env = run_environment(suite="my_suite")
write_simple_verdict(env.run_dir / "verdict.json", passed=True)
```

`run_environment` reads the contract environment variables and falls back to a
local directory when they are absent.

## Contents

| Module | What it holds |
|---|---|
| `gauntlet_sdk.contract` | Pydantic models for every file crossing the boundary |
| `gauntlet_sdk.runner` | `SuiteSpec` and `run_suite` |
| `gauntlet_sdk.iteration` | The loop, outcomes, and results |
| `gauntlet_sdk.environment` | Reading the contract environment |
| `gauntlet_sdk.profile` | Profile loading and validation |
| `gauntlet_sdk.reporting` | Artifact writers, usable standalone |
| `gauntlet_sdk.phases` | Timing named steps within an iteration |
| `gauntlet_sdk.remote` | SSH access to a unit under test (`remote` extra) |
| `gauntlet_sdk.monitor` | Background sampling of a remote host |
| `gauntlet_sdk.anomalies` | Anomaly recording with running counts |

The contract itself is specified in [`docs/contract.md`](../../docs/contract.md).
