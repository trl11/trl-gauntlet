# The Gauntlet suite contract

A suite is a program Gauntlet launches that writes a known set of files. It
does not need to be written in Python or import any Gauntlet library.

The models in `gauntlet_sdk.contract` are the normative definition.
`gauntlet verify` enforces them.

## Obligations

A conforming suite:

1. Declares itself in a `suite.yaml` at its root.
2. Writes artifacts into the directory Gauntlet provides.
3. Writes `verdict.json` before exiting.

Only the third is mandatory.

## 1. `suite.yaml`

Gauntlet discovers suites by walking its configured suite roots for
`suite.yaml` files. This file is the sole registration point.

```yaml
apiVersion: 1
key: thermal_cycle
title: Thermal Cycle
category: hardware
description: Chamber profile with per-segment pass/fail.

exec:
  command: ["python", "-m", "thermal_cycle.cli"]
  args:
    profile: --profile
    run_dir: --run-dir
    target: --target

profiles: ./profiles
conformance_profile: mock.yaml
produces: [metrics, verdict]
requires: [chamber, psu]
```

`exec.command` is the base argv, run without a shell. For each entry in
`exec.args`, Gauntlet appends the flag and its value, omitting both when the
value is unset for that run.

`exec.workdir` defaults to the suite directory. The suite directory is placed
on `PYTHONPATH`, and Gauntlet's own `bin` directory is prepended to `PATH`.

`exec.profile_schema_command` prints the suite's profile as JSON Schema to
stdout. Gauntlet calls it to render a profile editor form.
`gauntlet_sdk.make_suite_cli` provides it as `--print-profile-schema`.

`requires` names the capabilities Gauntlet must grant. An entry may carry the
role a bench binds an instrument to — `i2c.dut` rather than `i2c` — which is
how a suite asks for one of two identical instruments. A bare name resolves to
the only instrument of that capability, and is refused where there is more than
one.

`setup` describes how the bench is put together. It is shown under the
description on the Tests page, and unlike `description` its line breaks and
indentation survive, so an ASCII diagram of the wiring reads as it was written.

```yaml
setup: |
  The host reaches the part over a USB-to-I2C bridge.

    +------+   USB   +--------+   I2C   +------+
    | Host |-------->| bridge |-------->| part |
    +------+         +--------+         +------+
```

`overrides` declares the values an operator may set per run. They become form
controls in the UI and accepted keys on the REST API. Undeclared keys are
rejected.

```yaml
overrides:
  - {name: duration_s, flag: --duration-s, type: number, label: Duration, unit: s, minimum: 0.1}
  - {name: stop_on_failure, flag: --stop-on-failure, type: boolean}
```

A `boolean` override renders as the bare flag when true and is omitted when
false. A `string` override with `choices` renders as a select and rejects
values outside the list. A `number` or `integer` override may carry `minimum`
and `maximum`, which bound the form control and reject a value outside them.

`downloads` names files inside the suite directory that Gauntlet serves and
the Tests page offers as links, for what a bench needs before a run: a firmware
image to program a part with, a wiring diagram, a datasheet. Only a declared
path is served — a suite directory holds its profiles and its code, so
resolving whatever was asked for would turn every suite into a file server for
its own source.

```yaml
downloads:
  - path: firmware/image.hex
    label: Device firmware
    description: What the part under test is programmed with.
```

Each is fetched from `GET /api/suites/{key}/downloads/{path}`. `label`
defaults to the filename.

`requires` lists capabilities the suite needs. Gauntlet checks each against its
capability registry before spawning and rejects the run when one is
unavailable. Granted capabilities arrive as environment variables addressing
Gauntlet's REST API.

Full field list: `gauntlet schema suite`.

## 2. Environment

| Variable | Set when | Meaning |
|---|---|---|
| `GAUNTLET_RUN_DIR` | always | Directory to write artifacts into. Already exists. |
| `GAUNTLET_RUN_ID` | always | Run identifier. |
| `GAUNTLET_SUITE` | always | The suite key. |
| `GAUNTLET_SUITE_DIR` | always | Root of the suite directory. |
| `GAUNTLET_PROFILE` | a profile is selected | Absolute path to the profile file. |
| `GAUNTLET_TARGET` | the run names a target | Address of the unit under test. |
| `GAUNTLET_UNIT_SERIAL` | the operator enters one | Serial of the unit under test. |
| `GAUNTLET_API` | always | Base URL of the Gauntlet REST API. |
| `GAUNTLET_CAP_<NAME>_URL` | per granted capability | Endpoint for that capability. |
| `GAUNTLET_CAP_<NAME>_ID` | per granted capability | Instance id to address. |

Gauntlet creates the run directory. A suite writing elsewhere produces no
visible artifacts.

## 3. Artifacts

Paths are relative to `GAUNTLET_RUN_DIR`.

| File | Required | Writer | Contents |
|---|---|---|---|
| `verdict.json` | yes | suite, at exit | Pass/fail and reason. |
| `metrics.jsonl` | no | suite, during the run | One JSON record per line; streamed live. |
| `manifest.json` | no | suite, at exit | Versions, command line, and the profile as the run resolved it. |
| `junit.xml` | no | suite, at exit | Per-iteration results for CI. |
| `events.sqlite` | no | suite, during the run | Every `metrics.jsonl` record, in SQL. |
| `summary.md` | no | suite, at exit | Human-readable rollup. |
| `frames/` | no | suite, during the run | Images referenced from `metrics.images`. |
| `traces/` | no | suite, during the run | Captured signals referenced from `metrics.traces`. |
| `profile.yaml` | no | Gauntlet at start, the suite at exit | The profile as run: every field, defaults and overrides included. |
| `test.log` | no | Gauntlet | Captured stdout and stderr. |
| `instruments.jsonl` | no | Gauntlet, during the run | What the bench's instruments read, one line per instrument per second. |
| `instruments.json` | no | Gauntlet, at exit | The same readings summarised: count, extremes, mean and last. |

The last two are Gauntlet's own and a suite neither writes nor reads them: it
is not told which instruments are being recorded, and a run is identical
whether they are or not.

`profile.yaml` starts as the file the run was handed and is replaced at exit
with the profile the suite resolved — every field, the defaults the model
filled in and the overrides the run was started with. A run that dies before
resolving anything leaves the copy behind, which is better than no profile at
all. `manifest.json` carries the same resolved values as JSON.

`produces` lists what the suite writes. Gauntlet uses it to decide which views
to offer and which artifacts `verify --run` requires.

### `verdict.json`

```json
{"passed": false, "reason": "rail voltage out of tolerance on cycle 7"}
```

`reason` is required when `passed` is false. All other fields are optional
counters and presentation; see `gauntlet schema verdict`.

A run that exits without `verdict.json` is recorded as `error`. Exit codes are
recorded but do not determine the status.

### `metrics.jsonl`

One JSON object per line, appended during the run. Gauntlet tails the file and
streams each record. The file must be line-buffered.

```json
{"iteration": 3, "timestamp": 1767225600.0, "elapsed_run_s": 6.0, "success": true, "metrics": {"temp_c": 41.2}}
```

`kind` selects the record type:

| `kind` | Requires | Effect |
|---|---|---|
| `iteration` (default) | `iteration`, `success` | Advances the run counters. |
| `live` | — | Updates plots only. |
| `anomaly` | `probe` | Recorded and counted; does not affect pass/fail. |

Numeric leaves of `metrics` are flattened to dotted paths and plotted.
`metrics.images` is a list of paths relative to the run directory, and
`metrics.traces` is the same for a captured signal — a logic analyzer's window,
a scope's screen. Each gets its own tab, because a signal is looked through for
a different reason than a picture of the unit. A run recording neither is
offered neither tab: the files decide, and nothing asks which suite ran or what
it drove.

A trace is a picture, shown like any image, or a run's captures, drawn as lanes
that scroll and zoom. The suffix decides.

A `.jsonl` trace is a whole run's captures, appended a line at a time and drawn
on one timeline. The first line is the header and every line after it is one
capture:

```
{"channels": ["SCL", "SDA", "", "", "", "", "", ""], "rate_hz": 1000000}
{"elapsed_run_s": 0.0, "iteration": 0, "samples": 1000, "samples_base64": "AAAA..."}
{"elapsed_run_s": 1.0, "iteration": 1, "samples": 1000, "samples_base64": "AAAA..."}
```

`elapsed_run_s` is where the capture goes on the run's timeline, which is what
lets one view hold the lot. A run samples a window at a time, so the captures
are islands with the rest of the run between them: the gaps hold no samples and
are drawn empty rather than at a level. Every iteration that appends names the
file in its `metrics.traces`, which is what that field means and is how the run
page counts captures; the artifact list still shows the one file.

`samples_base64` decodes to one byte per sample, bit *n* being the level of
channel *n + 1*; `channels` labels them, the first being channel 1, and an
empty label falls back to the channel number.

Write the samples rather than a picture where the operator will want to look
inside the capture. They cost `rate_hz * seconds` a capture before base64, so a
suite sampling for a long time should let a profile turn them off.

### `events.sqlite`

The same records as `metrics.jsonl`, written as they are and readable with
`sqlite3`. Gauntlet never reads it; it is there for the analysis the run page
does not do.

| Table | Holds |
|---|---|
| `iterations` | One row per iteration: `elapsed_s`, `success`, `reason`, and `metrics` as JSON. |
| `phases` | One row per phase of an iteration, keyed by `(iteration, name)`. |
| `live` | One row per `live` record. |
| `anomalies` | One row per `anomaly` record, with `iteration` lifted out of its detail. |
| `metrics` | One row per metric leaf of an `iteration` or `live` record. |

`metrics` is what makes a value selectable without reaching into JSON. Nested
keys are dotted, `number` holds it when it is one and `text` when it is not,
and a list is kept whole as JSON rather than exploded into a row per element.

```sql
SELECT elapsed_s, number FROM metrics WHERE key = 'cpu.percent' ORDER BY elapsed_s;
SELECT * FROM metric_names;                       -- what this run recorded
SELECT iteration, reason FROM iterations WHERE success = 0;
```

`metric_names` is a view over `metrics`: every key, how many samples it has and
its range, which is the quickest way to find what a run holds.

## Conformance

```
gauntlet verify suites/my_suite            # manifest and static checks
gauntlet verify suites/my_suite --run      # execute a profile, then check artifacts
```

`--run` executes the suite's `conformance_profile` into a temporary directory,
validates each artifact against its model, confirms every `produces` entry was
written, and fails when `verdict.json` is absent.

## Versioning

`apiVersion` is the contract version a suite targets. Gauntlet accepts `1`.
Additive changes retain the version; changes that invalidate a conforming suite
increment it.
