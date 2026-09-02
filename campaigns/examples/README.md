# Examples

What belongs to no test programme: the two reference suites a new suite is
copied from, and one that samples the host Gauntlet itself runs on.

| Suite | Description | Transport |
|---|---|---|
| `example_sampled` | Reference Python suite using `SuiteSpec`. | none |
| `example_shell` | Reference bash suite with no Gauntlet dependency. | none |
| `system_stats` | Host CPU, memory, disk and network sampling. | none |

None of them needs hardware, so this is the campaign to run against a fresh
checkout to see Gauntlet do something.

It is a campaign like any other: `campaign.yaml` declares it and `./suites` is
its membership, so a suite dropped in there joins on the next rescan. The
suites in it are ordinary suites, found and run exactly like the ones a
hardware campaign holds — `gauntlet list` shows them all together.

## Layout

Every suite has the same shape, wherever it lives:

```
<suite>/
├── suite.yaml     declaration
├── suite/         code, always this name
└── profiles/      profile YAML
```

`exec.command` is therefore `["python", "-m", "suite.cli"]` in every manifest.

Every suite ships a `mock.yaml` or `smoke.yaml` profile that runs without
hardware and serves as its `conformance_profile`.

## Transports

Suites needing a transport declare it as an extra of `gauntlet-sdk`:
`remote` (SSH), `serial`, `can`, `mqtt`. `make setup` installs all of them.
None of the suites here needs one.

To create a suite:

```bash
make suite-new NAME=my_suite [TEMPLATE=shell]
```

That scaffolds into `suites/` at the repository root, which belongs to no
campaign and is where a suite with no programme yet goes. Pass `--into` to put
one in a campaign instead:

```bash
.venv/bin/gauntlet new-suite my_suite --into campaigns/hardware/suites
```

See [`docs/writing-a-suite.md`](../../docs/writing-a-suite.md) and
[`docs/campaigns.md`](../../docs/campaigns.md).
