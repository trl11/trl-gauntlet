# Handoff

State of the project after the frontend was built. Delete this file once the
open items below are tracked elsewhere.

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

`extras/trl-ui-kit` is a git submodule. A fresh clone needs
`git submodule update --init` before the frontend will build.

Requires Python 3.10 or later, and Node with npm for the frontend. `uv` is used
when present, `pip` otherwise. Without npm, `make check` skips the frontend
checks and the application serves the API with a placeholder at `/`.

## Contents

| Path | Contents |
|---|---|
| `packages/gauntlet-sdk/` | Library suite authors install. pydantic + pyyaml; SSH via the `remote` extra. |
| `packages/gauntlet/` | Application: discovery, supervisor, REST+SSE API, conformance, CLI, and the built frontend under `src/gauntlet/web_dist/`. |
| `suites/` | Nine suites: `ssd`, `ethernet`, `hardware_trigger`, `can_bus`, `rs422`, `piezo`, `system_stats`, plus two references. |
| `frontend/` | React operator UI. Vite builds it into `gauntlet/web_dist`. |
| `extras/trl-ui-kit/` | Component library, a submodule consumed as source through the `@trl11` alias. |
| `packages/gauntlet/.../scaffold/` | Suite scaffolder and the `python` / `shell` templates. |
| `docs/` | `contract.md`, `architecture.md`, `frontend.md`, `writing-a-suite.md`, `scaffolding.md`. |
| `CLAUDE.md` | Agent guidance. |

## Verified

- `make check`: ruff format-check, ruff, mypy strict over both packages and
  every suite, pytest, each suite's own tests, then `make frontend-check`.
- `make frontend-check`: eslint, `tsc --noEmit`, vitest.
- `make verify-run`: all nine suites, every check passing.
- `make list`, `make templates`, `make schemas`, `make api-spec` — the last
  writes an OpenAPI document with 38 paths.
- `make run` builds the bundle, prints the address, and serves `index.html` at
  `/` with its assets and favicon resolving; `/api/health`, `/api/units`,
  `/api/instruments`, `/api/system/info` and `/api/system/data` answer 200, and
  an unknown `/api/...` path answers 404 rather than the SPA shell.
- `npm run dev` serves on 7101 and proxies `/api` to the API on 7100.
  `VITE_API_BASE` is baked into a production build and prefixes every request.
- `git submodule update --init` is idempotent on an existing checkout.

## Outstanding

### Electron packaging

Not started. The frontend was written for it — hash routing, and every request
prefixed with `VITE_API_BASE` so the bundle can be loaded from `file://` and
pointed at a Gauntlet process on another origin — but nothing packages the two
together, and there is no launcher that starts the backend beside the shell.

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

`psu`, `daq`, and `chamber` are registered as `MockPsu`, `MockDaq` and
`MockChamber`, one class per module in `gauntlet/capabilities/`, sharing the
command and argument helpers in `mock_instrument.py`. Real drivers exist in
`trl-xclops/lab/src/xcng_lab/instruments/`: `hm310t.py`, `di2008.py`, `can.py`,
`rs422.py`. They must satisfy the `CapabilityProvider` protocol in
`gauntlet/capabilities/registry.py` — `available()`, `describe()`,
`instance_id()`, plus `read()` and `write()` for the HTTP proxy. The operator
panel additionally reads the optional `state()`, `commands()` and `command()`
facets, and shows `describe()["unavailable_reason"]` when a driver reports
itself unavailable.

The panel is generated from those declarations, so a real driver needs no
frontend change.

### Naming

The two ported suites were renamed from `rad_hardware_trigger` and `rad_ssd`.
The remaining five carry the same prefix in the source repository.

## Known gaps

- `_write_scratch_profile` leaves files under `<runs>/_scratch/`. Nothing prunes
  them.
- Coverage is approximately 80%. `gauntlet_sdk/cli.py` and the SSE streaming
  path in `gauntlet/api/runs.py` are the least covered.
- Every ported suite has been exercised only through its mock driver. The SSH,
  serial, CAN and MQTT paths are untested against hardware. `system_stats` is
  the exception: it reads the host it runs on.
- `ssd` provisioning (`profiles/bare-disk.yaml`) is likewise mock-only.
- Instrument state comes only from the three mocks, so the Instruments screen
  has never been driven against a provider that can fail or go offline
  mid-command.
- `prettier . -c` reports five unformatted files under `frontend/src`.
  `npm run lint` does not run prettier, so `make frontend-check` passes
  regardless.
- `ProfileEditor.test.tsx` prints a React "component suspended inside an act
  scope" warning. The test passes; nothing in `frontend/src` or the ui-kit uses
  `lazy`, `Suspense` or `use()`.

## Origins

| Gauntlet | Source in `trl-xclops` |
|---|---|
| `gauntlet_sdk/iteration.py` | `testing/lib/src/xcng_testing/runner/runner.py` |
| `gauntlet_sdk/runner.py` | `testing/lib/src/xcng_testing/suite/runner.py` |
| `gauntlet_sdk/reporting/*` | `testing/lib/src/xcng_testing/reporting/*` |
| `gauntlet_sdk/remote.py` | `testing/lib/src/xcng_testing/jetson/{ssh,uut}.py` |
| `gauntlet_sdk/anomalies.py` | `testing/lib/src/xcng_testing/radiation/anomalies.py` |
| `gauntlet/supervisor/*` | `lab/src/xcng_lab/supervisor/*` |
| `gauntlet/storage/runs_index.py` | `lab/src/xcng_lab/storage/runs_index.py` |
| `gauntlet/app.py`, `config.py` | `lab/src/xcng_lab/app.py`, `config.py` |
| `suites/ssd/` | `testing/rad_ssd/` and `xcng_testing/radiation/ssd.py` |
| `suites/hardware_trigger/` | `testing/rad_hardware_trigger/` |
| `suite.yaml` schema | `lab/src/xcng_lab/supervisor/discovery.py::_SUITE_SPEC` |
| `frontend/` (UX, not code) | `lab/web/src/` |
