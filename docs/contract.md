# The Gauntlet suite contract

A suite is a program Gauntlet launches that writes a known set of files. It
does not need to be written in Python or import any Gauntlet library.

The models in `gauntlet_suite.contract` are the normative definition.
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
`gauntlet_suite.make_suite_cli` provides it as `--print-profile-schema`.

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
| `manifest.json` | no | suite, at start | Versions, command line, profile. |
| `junit.xml` | no | suite, at exit | Per-iteration results for CI. |
| `events.sqlite` | no | suite, during the run | Queryable form of `metrics.jsonl`. |
| `summary.md` | no | suite, at exit | Human-readable rollup. |
| `frames/` | no | suite, during the run | Images referenced from `metrics.images`. |
| `profile.yaml` | no | Gauntlet | Copy of the profile as run. |
| `test.log` | no | Gauntlet | Captured stdout and stderr. |

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
`metrics.images` is a list of paths relative to the run directory.

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
