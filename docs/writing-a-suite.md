# Writing a suite

## Scaffold

```bash
make suite-new NAME=thermal_cycle              # Python suite
make suite-new NAME=link_check TEMPLATE=shell  # bash suite

gauntlet new-suite thermal_cycle               # same, via the CLI
```

The generated suite passes `gauntlet verify --run` as created. `gauntlet
templates` lists the available templates, and
[`scaffolding.md`](scaffolding.md) covers the generator itself.

```
suites/thermal_cycle/
├── suite.yaml              declaration
├── suite/                  code, same name in every suite
│   ├── cli.py              entry point
│   └── runner.py           profile model, iterate, pass criteria
└── profiles/
    ├── smoke.yaml          conformance profile; runs without hardware
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

This is also what tells the operator's panel the instrument is yours for the
length of the run: it names the run and locks the instrument's main key while
it is in flight.

### What there is to ask for

| Name | What it gives a suite |
|---|---|
| `camera` | Still images from a USB camera, one per `snapshot`, each measured for brightness and sharpness. Behind a GMSL adapter it also reports the link's lock state and error counters. |
| `chamber` | A temperature setpoint and a reading. Simulation only — there is no driver for real hardware. |
| `daq` | Eight analog inputs, each a voltage range or a thermocouple type, read a scan at a time. |
| `i2c` | An I2C bridge a suite drives itself: `write`, `read`, `write_read` and a bus `scan`, with no fixed device on the other end. |
| `logic` | Eight digital probes, captured a window at a time. Answers with each probe's level, edges, duty and frequency, a picture of the capture, and the samples themselves. |
| `psu` | A bench supply: set voltage and current limit, switch the output, read back volts, amps and watts. |

Ask for a capability rather than a device. `daq` is a DATAQ DI-2008 on this
bench and `i2c` a CP2112, but a suite never learns that, which is what lets
the same suite run against another unit behind the same capability.

[`instruments.md`](instruments.md) has the commands each one takes, what its
readings mean, and what a driver does when the hardware misbehaves. Whichever
you ask for, drive it over HTTP at the URL the grant carries — the suites
under `campaigns/hardware/` each hold a small client worth copying.

An instrument that answers with a picture — a camera's snapshot, a logic
analyzer's capture — returns it as `image_base64`. Write it into the run
directory and name it in the iteration's metrics, and the run page draws it:
`images` for a picture of the unit, `traces` for a captured signal, each in a
tab of its own.

```python
relative = f"traces/capture_{ictx.iteration:04d}.png"
ctx.artifact(*relative.split("/")).write_bytes(base64.b64decode(capture["image_base64"]))
return IterationOutcome(success=True, metrics={"traces": [relative]})
```

An instrument that samples answers with `samples_base64` as well. Recording
that instead of the picture gives the operator lanes to scroll and zoom rather
than a fixed drawing, which is what you want when the question is what
happened inside the window. Append every capture to one `.jsonl` file, placed
on the run's timeline by `elapsed_run_s`, and name that file in the
`metrics.traces` of every iteration that appends to it.

```python
relative = "traces/captures.jsonl"
lines = []
if ictx.iteration == 0:
    lines.append(json.dumps({"channels": labels, "rate_hz": capture["rate_hz"]}))
lines.append(
    json.dumps(
        {
            "elapsed_run_s": round(ictx.elapsed_run_s, 6),
            "iteration": ictx.iteration,
            "samples": capture["samples"],
            "samples_base64": capture["samples_base64"],
        }
    )
)
with ctx.artifact(*relative.split("/")).open("a") as handle:
    handle.write("\n".join(lines) + "\n")
return IterationOutcome(success=True, metrics={"traces": [relative]})
```

Samples cost `rate_hz * seconds` bytes, so a suite sampling for a long time
should let a profile turn them off. [`contract.md`](contract.md) defines every
shape a trace can take.

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
such field: it measures the host it runs on, so its `smoke.yaml` is both the
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
make suite-verify-run
```

Executes each conformance profile and validates the artifacts against the
contract.

## Suites without the iteration loop

Read `GAUNTLET_RUN_DIR`, append JSON lines to `metrics.jsonl`, write
`verdict.json`, exit. `suites/example_shell/run.sh` implements the contract in
bash, and `make suite-new NAME=x TEMPLATE=shell` scaffolds one.

Specification: [`contract.md`](contract.md).
