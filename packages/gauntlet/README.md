# gauntlet

The Gauntlet application: suite discovery, the run supervisor, the REST API,
and the web UI.

See the [project README](../../README.md) to get started. This page is about
the package itself.

## Install

```bash
pip install gauntlet
gauntlet serve
```

## Commands

| Command | What it does |
|---|---|
| `gauntlet serve` | Run the web UI and API |
| `gauntlet list` | List discovered suites |
| `gauntlet verify [dir] [--run]` | Check suites against the contract |
| `gauntlet new-suite NAME` | Generate a suite from a template |
| `gauntlet templates` | List the available templates |
| `gauntlet schema [name]` | Print a contract schema as JSON Schema |

## Configuration

Settings load from defaults, then `config.yaml` in the data directory, then
command-line flags.

| Variable | Meaning |
|---|---|
| `GAUNTLET_SUITE_PATH` | Colon-separated directories to search for suites |
| `GAUNTLET_DATA_DIR` | Where config, run artifacts, and the index live |

## Modules

| Module | Responsibility |
|---|---|
| `gauntlet.suites` | Finding `suite.yaml` files and listing profiles |
| `gauntlet.supervisor` | Building command lines, spawning, streaming, finalizing |
| `gauntlet.capabilities` | Lending instruments to running suites |
| `gauntlet.conformance` | Checking a suite against the contract |
| `gauntlet.scaffold` | Generating a suite from a bundled template |
| `gauntlet.storage` | SQLite index of run history |
| `gauntlet.api` | REST routers, one module per resource |

Run artifacts on disk are the source of truth. The SQLite index mirrors them
and is rebuilt from disk by `RunsIndex.import_tree`.
