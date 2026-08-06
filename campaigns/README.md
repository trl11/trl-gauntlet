# Campaigns

Campaigns are discovered from this directory by default. Point Gauntlet
elsewhere with `GAUNTLET_CAMPAIGN_PATH`, or `campaign_roots` in `config.yaml`.

A campaign groups the suites of one test programme and records how each is meant
to be run:

```
<campaign>/
├── campaign.yaml   declaration
└── suites/         the campaign's suites, each a normal suite directory
```

`suites` is added to the suite discovery roots, so everything inside it is
found as an ordinary suite and is runnable on its own. Membership is that
directory: a suite dropped into it joins the campaign after a rescan, whether or
not `campaign.yaml` names it.

Nothing here is compiled in. Edit a `campaign.yaml`, add a suite, then:

```bash
curl -X POST localhost:7100/api/campaigns/rescan
```

## Built in

| Campaign | Contents |
|---|---|
| `hardware` | The six suites that drive real hardware over a transport: `can_bus`, `ethernet`, `hardware_trigger`, `piezo`, `rs422`, `ssd`. |
| `radiation_tid` | One suite per component of the TID campaign, eighteen of them. Every one is a placeholder: it runs and passes without hardware, and measures nothing yet. Each suite's `runner.py` opens with the component, test vehicle, host and fixture it is for, and what it has to grow into. |

`suites/` at the repository root keeps what belongs to no programme — the two
reference suites and `system_stats`.

See [`docs/campaigns.md`](../docs/campaigns.md).
