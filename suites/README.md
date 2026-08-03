# Suites

Suites discovered from this directory by default. Point Gauntlet elsewhere with
`GAUNTLET_SUITE_PATH`.

| Suite | Description | Transport |
|---|---|---|
| `ssd` | SSD bandwidth, SHA-256 write-verify and SMART counters. One unit or many, probed concurrently. Optional format-and-mount provisioning. | SSH |
| `ethernet` | Timed upload and download between the unit and the lab host. Gates on session-average throughput. | SSH + TCP |
| `hardware_trigger` | GPIO trigger pulse train. | SSH |
| `can_bus` | Counter frames from the unit over CAN; gap and reorder accounting. | SSH + socketcan |
| `rs422` | Counter replies over an RS422 serial link; gap and reorder accounting. | serial |
| `piezo` | Extend-and-return motion cycles with position, temperature and fault flags. | MQTT |
| `example_sampled` | Reference Python suite using `SuiteSpec`. | none |
| `example_shell` | Reference bash suite with no Gauntlet dependency. | none |

Every suite ships a `mock.yaml` or `quick.yaml` profile that runs without
hardware and serves as its `conformance_profile`.

## Layout

Every suite has the same shape:

```
<suite>/
├── suite.yaml     declaration
├── suite/         code, always this name
└── profiles/      profile YAML
```

`exec.command` is therefore `["python", "-m", "suite.cli"]` in every manifest.

## Transports

Suites needing a transport declare it as an extra of `gauntlet-suite`:
`remote` (SSH), `serial`, `can`, `mqtt`. `make setup` installs all of them.

To create a suite:

```bash
make new-suite NAME=my_suite [TEMPLATE=shell]
```

See [`docs/writing-a-suite.md`](../docs/writing-a-suite.md).
