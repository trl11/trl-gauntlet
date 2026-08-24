# Suites

Suites discovered from this directory by default. Point Gauntlet elsewhere with
`GAUNTLET_SUITE_PATH`.

| Suite | Description | Transport |
|---|---|---|
| `example_sampled` | Reference Python suite using `SuiteSpec`. | none |
| `example_shell` | Reference bash suite with no Gauntlet dependency. | none |
| `system_stats` | Host CPU, memory, disk and network sampling. | none |

The suites that drive real hardware live in the `hardware` campaign, at
[`campaigns/hardware/`](../campaigns/hardware/). A campaign contributes its own
suite directory to discovery, so those are found and run exactly like the ones
here — `gauntlet list` shows all of them together.

What is left in this directory is what belongs to no programme: the two
reference suites, and one that samples the host it runs on.

Every suite ships a `mock.yaml` or `smoke.yaml` profile that runs without
hardware and serves as its `conformance_profile`.

## Layout

Every suite has the same shape, wherever it lives:

```
<suite>/
├── suite.yaml     declaration
├── suite/         code, always this name
└── profiles/      profile YAML
```

`exec.command` is therefore `["python", "-m", "suite.cli"]` in every manifest.

## Transports

Suites needing a transport declare it as an extra of `gauntlet-sdk`:
`remote` (SSH), `serial`, `can`, `mqtt`. `make setup` installs all of them.

To create a suite:

```bash
make suite-new NAME=my_suite [TEMPLATE=shell]
```

Pass `--into` to put one inside a campaign instead:

```bash
.venv/bin/gauntlet new-suite my_suite --into campaigns/hardware/suites
```

See [`docs/writing-a-suite.md`](../docs/writing-a-suite.md) and
[`docs/campaigns.md`](../docs/campaigns.md).
