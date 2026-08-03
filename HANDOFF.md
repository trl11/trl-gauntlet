# Handoff

State of the project at the point it was copied out of `trl-xclops/gauntlet/`.
Delete this file once the project has its own history and the open items are
tracked elsewhere.

## Moving the directory

`.venv` contains absolute paths and editable installs pointing at the original
location. Remove it before or after copying.

```bash
rm -rf .venv output build .mypy_cache .ruff_cache .coverage
find . -name '*.egg-info' -type d -prune -exec rm -rf {} +

make setup
make verify-run
make check
```

`make distclean` performs most of this.

The directory is not under version control. Run `git init` in the copy.

Requires Python 3.10 or later; developed against 3.10.20. `uv` is used when
present, `pip` otherwise.

## Contents

| Path | Contents |
|---|---|
| `packages/gauntlet-suite/` | Library suite authors install. pydantic + pyyaml; SSH via the `remote` extra. |
| `packages/gauntlet/` | Application: discovery, supervisor, REST+SSE API, conformance, CLI. |
| `suites/` | Eight suites: `ssd`, `ethernet`, `hardware_trigger`, `can_bus`, `rs422`, `piezo`, plus two references. |
| `packages/gauntlet/.../scaffold/` | Suite scaffolder and the `python` / `shell` templates. |
| `docs/` | `contract.md`, `architecture.md`, `writing-a-suite.md`. |
| `CLAUDE.md` | Agent guidance. |

## Verified

- `make setup`, `make run`: server on `:7100`, all suites discovered.
- `POST /api/runs` with an override: override applied to argv; log, metrics,
  phase and iteration events streamed over SSE; verdict written and indexed.
- `make new-suite NAME=x`: scaffolded suite passes `verify --run`.
- `make check`: 127 tests, ruff, mypy strict.
- `make verify-run`: all eight suites pass.
- `make new-suite` with both templates: rendered suites pass `verify --run`.
- Clean-room build in `/tmp` with no other virtualenv on `PATH`.
- `GET /api/suites/{key}/profile-schema` returns JSON Schema with descriptions,
  defaults and constraints.

## Outstanding

### Frontend

Not started. `web/` does not exist; `make web` reports this and succeeds,
`make web-dev` fails. The application serves a placeholder at `/` linking to
`/docs`.

The REST and SSE API is complete. `docs/architecture.md` lists which endpoint
feeds each UI surface. `trl-xclops/lab/web/` contains a React implementation of
a comparable UI and `lab/DESIGN.md` its design tokens; the API shapes differ.

### Suites not ported

`rad_camera` probes cameras through the xclops SDK, so it cannot move without
that dependency. `burn_in`, `thermal`, `power_cycle`, `power_measurement`,
`soak`, `api` and `ui` likewise depend on the xclops SDK, controller or
frontend. All remain in `trl-xclops/testing/`.

Two behaviours from the source suites were not carried over and would need
bench time to add: the `lab_sender` mode of `rad_can`, which drives a Waveshare
serial USB-CAN adapter from the lab host, and the RS422 auto-detect fallback
for adapters other than FTDI `0403:6001`.

### Instrument drivers

`psu`, `daq`, and `chamber` are registered as `MockInstrument`. Real drivers
exist in `trl-xclops/lab/src/xcng_lab/instruments/`: `hm310t.py`, `di2008.py`,
`can.py`, `rs422.py`. They must satisfy the `CapabilityProvider` protocol in
`gauntlet/capabilities/registry.py` — `available()`, `describe()`,
`instance_id()`, plus `read()` and `write()` for the HTTP proxy.

### Naming

The two ported suites were renamed from `rad_hardware_trigger` and `rad_ssd`.
The remaining five carry the same prefix in the source repository.

## Suggested sequence

1. `git init`, commit, CI running `make check` and `make verify-run`.
2. Port the remaining suites.
3. Frontend.
4. Real instrument drivers.
5. Publish `gauntlet-suite` to an index once suites live in other repositories.

## Known gaps

- `_write_scratch_profile` leaves files under `<runs>/_scratch/`. Nothing prunes
  them.
- Coverage is approximately 71%. `cli.py` and the SSE streaming path in
  `api/runs.py` are the least covered.
- Every ported suite has been exercised only through its mock driver. The SSH,
  serial, CAN and MQTT paths are untested against hardware.
- `ssd` provisioning (`profiles/bare-disk.yaml`) is likewise mock-only.

## Origins

| Gauntlet | Source in `trl-xclops` |
|---|---|
| `gauntlet_suite/iteration.py` | `testing/lib/src/xcng_testing/runner/runner.py` |
| `gauntlet_suite/runner.py` | `testing/lib/src/xcng_testing/suite/runner.py` |
| `gauntlet_suite/reporting/*` | `testing/lib/src/xcng_testing/reporting/*` |
| `gauntlet_suite/remote.py` | `testing/lib/src/xcng_testing/jetson/{ssh,uut}.py` |
| `gauntlet_suite/anomalies.py` | `testing/lib/src/xcng_testing/radiation/anomalies.py` |
| `gauntlet/supervisor/*` | `lab/src/xcng_lab/supervisor/*` |
| `gauntlet/storage/runs_index.py` | `lab/src/xcng_lab/storage/runs_index.py` |
| `gauntlet/app.py`, `config.py` | `lab/src/xcng_lab/app.py`, `config.py` |
| `suites/ssd/` | `testing/rad_ssd/` and `xcng_testing/radiation/ssd.py` |
| `suites/hardware_trigger/` | `testing/rad_hardware_trigger/` |
| `suite.yaml` schema | `lab/src/xcng_lab/supervisor/discovery.py::_SUITE_SPEC` |
