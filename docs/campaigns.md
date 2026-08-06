# Campaigns

A campaign groups the suites of one test programme and records how each is
meant to be run. It is an operator-facing arrangement of suites, not part of the
suite contract: a suite never learns which campaign it belongs to, and runs
identically inside one or outside it.

Campaigns exist so a programme can be assembled, changed and re-run without
rebuilding Gauntlet. Everything below is read from disk and refreshed by a
rescan.

## `campaign.yaml`

```yaml
# yaml-language-server: $schema=http://127.0.0.1:7100/api/campaigns/schema
apiVersion: 1
key: radiation_tid
title: Radiation TID
description: >
  Total ionising dose characterisation of the flight component set.

suites: ./suites

members:
  - suite: tid_lan7430
    component: LAN7430-I/Y9X
    test_vehicle: EVB-LAN7430
    host: Raspberry Pi
    fixture: "1-1"
    profile: standard.yaml
    overrides:
      duration_s: 600
    notes: Waiting on the EVB.
```

| Field | Meaning |
|---|---|
| `key` | `lower_snake_case`, unique across every campaign root |
| `suites` | Directory holding this campaign's suites, relative to the manifest |
| `members` | Per-suite configuration. Optional, and not what defines membership |

A member entry accepts `component`, `test_vehicle`, `host`, `fixture`,
`profile`, `target`, `unit_serial`, `overrides` and `notes`. Every one is
optional.

## Membership is the directory, not the list

`suites: ./suites` is the load-bearing line. That directory is appended to the
suite discovery roots, so every suite inside it is discovered as an ordinary
suite and is runnable on its own.

Membership follows from where a suite sits on disk. A `members` entry
*configures* a suite found there; it does not admit it. So:

- A suite dropped into the directory joins the campaign with no entry at all,
  and is reported with `declared: false`.
- A declared member whose suite is not on disk is still listed, with
  `present: false`, rather than disappearing.

This is what lets the directory change without the manifest going stale.

The configured suite roots are searched before any campaign's, and discovery
keeps the earlier root on a key collision, so a suite shipped with Gauntlet wins
over a campaign that shadows its key. Two campaigns shipping the same suite key
collide; the collision is reported in `errors` rather than raised.

## Where campaigns are found

`GAUNTLET_CAMPAIGN_PATH` takes a colon-separated list of roots, and
`campaign_roots` in `config.yaml` does the same. Both default to `./campaigns`.
A root that does not exist is skipped rather than treated as an error, and a
malformed `campaign.yaml` is collected into `errors` rather than raised — the
same contract suite discovery follows.

Because a campaign is a self-contained directory, one can live on a memory stick
or a mounted share. Point a campaign root at it and its suites are picked up.

## Changing a campaign

The manifest on disk is the source of truth. There are two ways to change it and
they are the same change:

```bash
# edit it directly, then rescan
vim campaigns/radiation_tid/campaign.yaml
curl -X POST localhost:7100/api/campaigns/rescan
```

```bash
# or write it through the API, which validates and rescans for you
curl -X PUT localhost:7100/api/campaigns/radiation_tid/manifest \
     -H 'content-type: application/json' -d '{"body": "apiVersion: 1\n..."}'
```

`PUT` writes the file only once it parses and validates, so a rejected edit
leaves the campaign exactly as it was. Renaming `key` is refused: the caller
addressed the campaign by the old key, and discovery is by directory, so the
rename would take effect somewhere the caller is not looking. Rename by moving
the directory and rescanning.

Nothing here restarts or rebuilds anything. Adding a suite, editing the
manifest, and re-running a member are all live against the running process.

## Coverage

A campaign reports what the runs index knows about each member: `run_count`,
`passed`, `failed` and `last_run`.

This is derived from the suite key at request time, not recorded on a run. That
matters because `RunsIndex.import_tree` rebuilds the runs table from the
artifacts on disk, and a suite process has no idea what campaign launched it —
a stored column would be lost on every reimport. Deriving it also means moving a
suite between campaigns takes its history with it.

The cost is that history follows the directory: unmount the campaign and its
coverage is empty until it is back. The runs themselves are untouched.

## Running a member

```
POST /api/campaigns/{key}/members/{suite}/run
```

Starts one run using the member's declared `profile`, `target`, `unit_serial`
and `overrides`, with anything in the request body taking precedence. It is a
convenience over `POST /runs`, not a scheduler: campaigns group runs, they do
not sequence them. Re-running a member after changing the campaign is this call
again.

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/campaigns` | Every campaign, without members resolved |
| `GET` | `/api/campaigns/{key}` | One campaign, with members and coverage |
| `GET` | `/api/campaigns/{key}/manifest` | The `campaign.yaml` as text |
| `PUT` | `/api/campaigns/{key}/manifest` | Validate, save, rescan |
| `POST` | `/api/campaigns/{key}/members/{suite}/run` | Run one member |
| `POST` | `/api/campaigns/rescan` | Re-read the roots and their suites |
| `GET` | `/api/campaigns/schema` | JSON Schema for `campaign.yaml` |

`POST /api/campaigns/rescan` and `POST /api/suites/rescan` do the same work:
campaigns are read first, then the suites they contribute. They differ only in
what they return.
