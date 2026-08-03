# Writing a suite

## Scaffold

```bash
make new-suite NAME=thermal_cycle              # Python suite
make new-suite NAME=link_check TEMPLATE=shell  # bash suite

gauntlet new-suite thermal_cycle               # same, via the CLI
```

The generated suite passes `gauntlet verify --run` as created. `gauntlet
templates` lists the available templates.

```
suites/thermal_cycle/
├── suite.yaml              declaration
├── suite/                  code, same name in every suite
│   ├── cli.py              entry point
│   └── runner.py           profile model, iterate, pass criteria
└── profiles/
    ├── quick.yaml          conformance profile; runs without hardware
    └── standard.yaml       bench profile
```

The code package is `suite/` in every suite, so `exec.command` is
`["python", "-m", "suite.cli"]` everywhere and the layout does not vary.

## Profile model

Each field becomes a form control in the UI, and its `description` is the
label text.

```python
class ThermalCycleProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_s: float = Field(default=60.0, gt=0, description="How long to run.")
    max_temperature_c: float = Field(default=70.0, description="Fail above this.")
```

`extra="forbid"` rejects unknown keys at load time, naming the offending field.

## iterate()

One tick of work. Return `success=False` with a `reason` to fail the
iteration. Numeric values in `metrics` are plotted.

```python
def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    temperature = read_sensor(ctx.target)
    over = temperature >= ctx.profile.max_temperature_c
    return IterationOutcome(
        success=not over,
        reason=f"{temperature:.1f}C exceeds limit" if over else "",
        metrics={"temperature_c": temperature},
        summary=f"{temperature:.1f}C",
    )
```

`ctx.target` is the unit under test. `ctx.artifact("frames", "x.jpg")` resolves
a path inside the run directory and creates parent directories.
`ctx.extras` holds state across iterations.

`reason` is the first field shown for a failed run. Include the measured value
and the limit.

## evaluate()

Aggregate criteria, checked once with every outcome. Return `None` to accept
the default of passing when no iteration failed.

```python
def _evaluate(outcomes, profile):
    peak = max(o.metrics["temperature_c"] for o in outcomes)
    if peak > profile.max_temperature_c:
        return False, f"peak {peak:.1f}C exceeds limit {profile.max_temperature_c:.1f}C"
    return True, ""
```

## Instruments

Open in `setup`, release in `teardown`. `teardown` runs in a `finally` and
fires on abort.

```python
def _setup(ctx: SuiteContext) -> None:
    ctx.extras["psu"] = PsuClient(ctx.env.capability("psu"))

def _teardown(ctx: SuiteContext) -> None:
    ctx.extras["psu"].output_off()
```

Declare the requirement so Gauntlet rejects the run when the instrument is
absent:

```yaml
requires: [psu]
```

## Remote units

`gauntlet_sdk.remote` provides SSH access. Install
`gauntlet-sdk[remote]` for paramiko.

```python
from gauntlet_sdk.remote import RemoteTarget, connect, run

target = RemoteTarget.from_env(host=ctx.target)
client = connect(target)
result = run(client, "uname -r", timeout=10.0)
```

`RemoteMonitor` samples load, memory, and disk on the unit in the background
and writes `live` records. `capture_host_facts` returns hostname, kernel, OS,
and CPU for the run manifest.

See `suites/ssd/` and `suites/hardware_trigger/` for working examples.

## Profiles

Ship a conformance profile that requires no hardware and completes in seconds,
and name it in the manifest:

```yaml
conformance_profile: mock.yaml
```

Built-in suites use a `driver: real | mock` field for this, where `mock`
synthesises results without contacting a unit. `suites/system_stats/` needs no
such field: it measures the host it runs on, so its `quick.yaml` is both the
conformance profile and a real measurement.

## Overrides

Values an operator may change per run without editing a profile:

```yaml
overrides:
  - {name: duration_s, flag: --duration-s, type: number, label: Duration, unit: s, minimum: 0.1}
```

Undeclared keys are rejected by the API. `label`, `unit`, `choices`, `minimum`
and `maximum` are what the run form builds its control from, so fill them in.

## Verify

```bash
make verify-run
```

Executes each conformance profile and validates the artifacts against the
contract.

## Suites without the iteration loop

Read `GAUNTLET_RUN_DIR`, append JSON lines to `metrics.jsonl`, write
`verdict.json`, exit. `suites/example_shell/run.sh` implements the contract in
bash, and `make new-suite NAME=x TEMPLATE=shell` scaffolds one.

Specification: [`contract.md`](contract.md).
