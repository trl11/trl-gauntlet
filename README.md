# Gauntlet

Gauntlet runs test suites. It launches a suite, streams its progress, and
indexes the artifacts it produces.

Gauntlet holds no suite-specific code. A suite declares itself in a
`suite.yaml`, writes its results into a directory Gauntlet provides, and can be
written in any language and stored in any repository.

```bash
make setup          # create .venv, install both packages
make run            # http://127.0.0.1:7100
```

System packages the suites' transports need are listed in
[`dependencies.txt`](dependencies.txt). [`requirements.txt`](requirements.txt)
installs both packages editable for anyone preferring `pip` to `make setup`.

## Built-in suites

| Suite | Description |
|---|---|
| `ssd` | SSD bandwidth, SHA-256 write-verify and SMART counters over SSH. One unit or many, probed concurrently. |
| `ethernet` | Timed upload and download between the unit and the lab host. |
| `hardware_trigger` | GPIO trigger pulse train over SSH. |
| `can_bus` | Counter frames over CAN, with gap and reorder accounting. |
| `rs422` | Counter replies over an RS422 serial link. |
| `piezo` | Extend-and-return motion cycles over MQTT. |
| `example_sampled` | Reference Python suite using `SuiteSpec` and the iteration loop. |
| `example_shell` | Reference bash suite with no Gauntlet dependency. |

Each ships a `mock.yaml` or `quick.yaml` profile that runs without hardware.
Every suite has the same layout: `suite.yaml`, a `suite/` package, `profiles/`.

## Creating a suite

```bash
make new-suite NAME=thermal_cycle              # Python suite
make new-suite NAME=link_check TEMPLATE=shell  # bash suite
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
make verify-run
```

`verify-run` executes each suite's conformance profile, validates every
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
| `packages/gauntlet-suite/` | Library for suite authors. Requires pydantic and pyyaml. |
| `packages/gauntlet/` | Application: discovery, supervisor, REST API, web UI. |
| `suites/` | Built-in and reference suites. |

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

Mock providers for `psu`, `daq`, and `chamber` are registered by default.

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
| `make dev` / `make dev-stop` | Start or stop the devcontainer |
| `make run` | Run with auto-reload |
| `make new-suite NAME=x` | Scaffold a suite (`TEMPLATE=python\|shell`) |
| `make templates` | List the available suite templates |
| `make list` | List discovered suites |
| `make verify` / `make verify-run` | Contract checks, static or executing |
| `make check` | format-check, lint, typecheck, test |

`gauntlet --help` lists the full CLI.
