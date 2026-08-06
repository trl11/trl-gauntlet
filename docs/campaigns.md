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

`campaign.yaml` is read, never written. Edit it with an editor, or add a suite
to the campaign's suite directory, then rescan:

```bash
vim campaigns/radiation_tid/campaign.yaml
curl -X POST localhost:7100/api/campaigns/rescan
```

Nothing restarts or rebuilds. Adding a suite, editing the manifest, and
re-running a member are all live against the running process.

There is no endpoint that writes a manifest, so nothing Gauntlet serves can
disagree with the file on disk. Rename a campaign by moving its directory and
rescanning; the key is where discovery finds it, not something to be patched
over the wire.

## A campaign is not a session

It has no start, no end, and no state. Nothing is written when a member runs,
and no run records which campaign it was reached through. A run started from a
campaign is identical to the same run started from the suite list.

This matters for reading what a campaign reports. It has no target — nothing in
`campaign.yaml` says what finished looks like — so there is no progress to be
made against one, and nothing that could be complete.

## Which campaign a run belongs to

A campaign reports what it groups; it does not report what those suites have
done. Run history is read from the run instead: `GET /api/runs` and
`GET /api/runs/{id}` carry a `campaign` of `{key, title}`, or null.

That is derived when the run is read — `gauntlet.catalog.campaigns_by_suite`
maps each discovered suite to the campaign whose directory holds it — and never
recorded on the run. `RunsIndex.import_tree` rebuilds the runs table from the
artifacts on disk and a suite process has no idea what campaign launched it, so
a stored column would be lost on every reimport.

Two consequences follow, and both are worth knowing:

- It names the campaign that groups that suite **now**, not the one that
  started the run. A run predating the campaign reports it too, and moving a
  suite between campaigns moves its whole history with it.
- It follows the directory. Unmount a campaign and its runs report no campaign
  until it is back. The runs themselves are untouched.

The UI shows it as a Campaign column on the history table and a row on the run
detail, both linking back to the campaign in the Tests page.

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
| `GET` | `/api/campaigns/{key}` | One campaign, with the members it groups |
| `POST` | `/api/campaigns/{key}/members/{suite}/run` | Run one member |
| `POST` | `/api/campaigns/rescan` | Re-read the roots and their suites |
| `GET` | `/api/campaigns/schema` | JSON Schema for `campaign.yaml` |

`POST /api/campaigns/rescan` and `POST /api/suites/rescan` do the same work:
campaigns are read first, then the suites they contribute. They differ only in
what they return.
