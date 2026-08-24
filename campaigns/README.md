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
| `hardware` | The seven suites that drive real hardware: `can_bus`, `daq_capture`, `ethernet`, `hardware_trigger`, `piezo`, `rs422`, `ssd`. `daq_capture` is the one that drives it through an instrument Gauntlet lends it rather than a transport of its own. |
| `radiation_tid` | One suite per component of the TID campaign, eight of them. `tid_lan7430`, `camera_snapshot`, `tid_max96793` and `tid_max96792` measure; the other four are placeholders that run and pass without hardware and measure nothing yet. Each placeholder's `runner.py` opens with the component, test vehicle, host and fixture it is for, and what it has to grow into. |

`suites/` at the repository root keeps what belongs to no programme — the two
reference suites and `system_stats`.

See [`docs/campaigns.md`](../docs/campaigns.md).
