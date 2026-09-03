# Gauntlet

Gauntlet runs test suites. It launches a suite, streams its progress, indexes
the artifacts it produces, and serves an operator UI over the same port.

Gauntlet holds no suite-specific code. A suite declares itself in a
`suite.yaml`, writes its results into a directory Gauntlet provides, and can be
written in any language and stored in any repository.

## Quickstart

```bash
git clone <url> gauntlet
cd gauntlet
git submodule update --init      # extras/trl-ui-kit, the component library
make setup                       # create .venv, install both packages
make run                         # build the frontend and serve
```

Open <http://localhost:7100>. `make run` prints the address it is serving on.

Requires Python 3.10 or later, `uv`, which creates `.venv` and installs both
packages, and Node with npm, which builds the frontend. The devcontainer image
carries all three.

The first thing to try is the `system_stats` suite: it samples the host it runs
on, needs no hardware, and finishes in three seconds on the `quick` profile.

System packages are listed twice, because the two things this repository builds
need different sets. [`dependencies.txt`](dependencies.txt) is a development
machine: the suites' transports, plus Electron's runtime, an X server and FUSE
for the desktop app.
[`targets/docker/dependencies.txt`](targets/docker/dependencies.txt) is the
server image, and is the transports alone.
[`requirements.txt`](requirements.txt) installs both packages editable for
anyone preferring `pip` to `make setup`.

Writing a suite needs `gauntlet-sdk` and nothing else from this repository.
`make sdk-build` writes its wheel to `dist/`, to install wherever the suite
runs:

```bash
make sdk-build
pip install dist/gauntlet_sdk-*.whl        # add [remote] for the SSH transport
```

`make build` writes every artifact to `dist/`: both wheels, the AppImage and
deb, and the server image as a loadable tarball. The two wheels install
together or not at all — `gauntlet` depends on `gauntlet-sdk`, and neither is
on a package registry:

```bash
pip install dist/gauntlet_sdk-*.whl dist/gauntlet-*.whl
gauntlet serve
```

## The screens

Navigation is a top tab bar. `?` lists the keyboard shortcuts; `g` followed by
one key jumps between pages.

| Screen | What it does |
|---|---|
| Dashboard | Active runs with live progress, host CPU/memory/disk, instrument availability, pass-and-fail counts over the last day and week, the ten most recent runs, and a launcher. |
| Tests | Every discovered suite by category. Pick one to see its profiles, declared overrides, artifacts it produces and capabilities it requires. A profile can be edited against its schema, diffed against the file on disk, duplicated or deleted. Rescan re-reads the suite roots; Verify runs the contract check. |
| History | Every recorded run, filtered by suite, unit, status and date range, sorted and paged on the server. Rows expand to their details and can be exported as CSV. |
| Run | One run. Verdict and phases, live log, metric charts, per-iteration table, artifact list, and notes. Live runs stream over SSE and can be stopped or aborted. |
| Units | Everything that has been on the bench, with run counts and outcomes. Open one for its history and notes; rename it, and its runs follow; forget it, and the runs stay. |
| Instruments | One panel per registered instrument, generated from what that provider declares: its state as rows, each of its commands as a form. Scan re-probes availability. |
| Settings | Service configuration, paths, suite-discovery errors, versions, and static host facts. |

## Built-in suites

| Suite | Description |
|---|---|
| `system_stats` | Samples the Linux host: CPU, load, memory, swap, disks, thermal zones, network counters, processes, uptime. No hardware. |
| `ssd` | Half a minute of SSD bandwidth, SHA-256 write-verify and SMART counters over SSH — whether a disk works. `tid_ssd` in the radiation campaign runs the same probe for hours. |
| `ethernet` | Timed upload and download between the unit and the lab host. |
| `hardware_trigger` | GPIO trigger pulse train over SSH. |
| `can_bus` | Counter frames over CAN, with gap and reorder accounting. |
| `rs422` | Counter replies over an RS422 serial link. |
| `piezo` | Extend-and-return motion cycles over MQTT. |
| `example_sampled` | Reference Python suite using `SuiteSpec` and the iteration loop. |
| `example_shell` | Reference bash suite with no Gauntlet dependency. |

Each ships a `mock.yaml` or `smoke.yaml` profile that runs without hardware.
Every suite has the same layout: `suite.yaml`, a `suite/` package, `profiles/`.

## Creating a suite

```bash
make suite-new NAME=thermal_cycle              # Python suite
make suite-new NAME=link_check TEMPLATE=shell  # bash suite
```

Or through the CLI directly: `gauntlet new-suite thermal_cycle`.

The generated suite passes `gauntlet verify --run`. Fill in `iterate()`:

```python
def _iterate(ctx: SuiteContext, ictx: IterationContext) -> IterationOutcome:
    temperature = read_sensor(ctx.target)
    over = temperature >= ctx.profile.max_temperature_c
    return IterationOutcome(
        success=not over,
        reason=f"{temperature:.1f}C exceeds limit" if over else "",
        metrics={"temperature_c": temperature},
    )
```

Then check it against the contract:

```bash
make suite-verify-run
```

`suite-verify-run` executes each suite's conformance profile, validates every
artifact against its schema, confirms each declared output was written, and
fails when no verdict was produced.

## Suite obligations

1. A `suite.yaml` at the suite root.
2. Artifacts written into `$GAUNTLET_RUN_DIR`.
3. A `verdict.json` before exit.

Only the third is mandatory. A run that exits without a verdict is recorded as
an error rather than a failure.

Specification: [`docs/contract.md`](docs/contract.md).

## Layout

| Path | Contents |
|---|---|
| `packages/gauntlet-sdk/` | Library for suite authors. Requires pydantic and pyyaml. |
| `packages/gauntlet/` | Application: discovery, supervisor, REST API, and the built frontend. |
| `campaigns/` | Every suite that ships, each in the campaign that groups it. |
| `frontend/` | React operator UI, built into `gauntlet/web_dist`. |
| `targets/app/` | Electron shell: the desktop target. |
| `targets/docker/` | The server image: the second target. |
| `targets/service/` | The service a bench runs, packaged as one deb: the third. |
| `tools/` | Scripts for deploying, working a bench, and cutting a release. |
| `extras/trl-ui-kit/` | Component library, a git submodule the UI consumes as source. |
| `docs/` | Contract specification and guides. |

## Suites in other repositories

Gauntlet searches the directories listed in `GAUNTLET_SUITE_PATH`:

```bash
export GAUNTLET_SUITE_PATH=/opt/rig-suites:$HOME/my-suites
make run
```

## Instruments

Gauntlet owns instrument serial ports. A suite declares what it needs:

```yaml
requires: [psu, chamber]
```

Gauntlet verifies each capability is available before spawning and rejects the
run otherwise. A granted capability is passed as `GAUNTLET_CAP_PSU_URL`, an
HTTP endpoint the suite drives.

An instrument is registered only while its hardware answers, so the Instruments
screen shows the bench as it is. Nothing simulated is registered unless
`simulated_instruments` names it. Each provider declares its own state and
commands, and the screen builds its panel from that declaration.

See [`docs/instruments.md`](docs/instruments.md).

## Frontend

```bash
make frontend        # build the bundle into gauntlet/web_dist
make frontend-dev    # Vite on 7101, proxying /api to the API on 7100
make frontend-test   # vitest
make frontend-check  # prettier --check, eslint, tsc, vitest
```

`make run` builds the bundle first. See
[`docs/frontend.md`](docs/frontend.md) and
[`frontend/README.md`](frontend/README.md).

## Devcontainer

```bash
make dev            # build if needed, start, and open a shell
make dev-stop       # stop and remove
make dev-status     # show whether it is running
```

Python 3.12 on Debian bookworm, plus the packages in
[`dependencies.txt`](dependencies.txt) and the `claude` and `codex` agents.
`~/.claude` and `~/.codex` are mounted from the host so both stay signed in.
Details in [`.devcontainer/README.md`](.devcontainer/README.md).

Everything runs as `dev`, whose uid and gid track the caller's, so files the
container writes into the bind-mounted repository belong to the same user on
the host and need no `sudo` to clean up.

`.venv` is created by `postCreateCommand` if it is missing, and by any `make`
target through `ensure-setup`.

Host and container share that one `.venv` through the bind mount but see it at
different absolute paths, which editable installs and console-script shebangs
record. `make` stamps the venv with the path it was built for and rebuilds it
when that changes, so moving between the two costs a few seconds rather than a
confusing `bad interpreter`.

## Commands

| Command | Action |
|---|---|
| `make setup` | Create `.venv`, install both packages editable |
| `make dev` / `make dev-stop` / `make dev-status` | Start, stop, or query the devcontainer |
| `make run` | Build the frontend and serve on `$(APP_PORT)`, default 7100, with auto-reload |
| `make run-hmr` | Backend (auto-reload) and frontend dev server (HMR) together; browse `$(FRONTEND_PORT)`, default 7101 |
| `make stop` | Stop the server `run` or `run-hmr` started, and any suite it was running |
| `make frontend` | Build the frontend bundle |
| `make frontend-dev` | Frontend dev server on 7101, proxying `/api` to 7100 |
| `make frontend-test` | The frontend tests |
| `make frontend-check` | `prettier --check`, eslint, `tsc --noEmit`, vitest |
| `make suite-new NAME=x` | Scaffold a suite (`TEMPLATE=python\|shell`) |
| `make suite-templates` | List the available suite templates |
| `make suite-list` | List discovered suites |
| `make suite-verify` / `make suite-verify-run` | Contract checks, static or executing |
| `make verify` | Build, check, and test everything |
| `make check` | format-check, lint, typecheck, and every test but the end-to-end one |
| `make test` | Every test: gauntlet, suites, frontend, end to end |
| `make gauntlet-test` / `make suite-test` | Python tests, then each suite's own tests |
| `make schemas` / `make api-spec` | Print contract schema names; write `build/openapi.json` |
| `make clean` / `make distclean` | Remove build output; also remove `dist/`, `.venv` and `output/` |

`make help` lists them with descriptions. `gauntlet --help` lists the full CLI.
