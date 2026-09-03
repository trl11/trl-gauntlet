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
| `examples` | The two reference suites a new suite is copied from, and one that samples the host Gauntlet runs on. None of them needs hardware. |
| `hardware` | The suites that drive real hardware. Most reach it over a transport of their own; `daq_capture` and `gmsl_camera` are driven through an instrument Gauntlet lends them instead. |
| `radiation_tid` | One suite per component of the TID campaign. `tid_lan7430`, `tid_max96793`, `tid_max96792` and `tid_ssd` measure; the rest are placeholders that run and pass without hardware and measure nothing yet. Each placeholder's `runner.py` opens with the component, test vehicle, host and fixture it is for, and what it has to grow into. |

`examples` keeps what belongs to no programme — the two reference suites and
`system_stats`. Nothing needs hardware, so it is the campaign to run against a
fresh checkout.

See [`docs/campaigns.md`](../docs/campaigns.md).
