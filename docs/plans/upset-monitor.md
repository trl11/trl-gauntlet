# Upset Monitor

Status: proposed

An operator who watches a run sets a threshold on a DAQ channel. When a reading crosses the threshold, Gauntlet keeps the readings from before and after the crossing and records the time of the crossing. The run page flashes. This plan extends four existing paths. `InstrumentRecorder` shows how the supervisor starts and stops a Gauntlet-side observer for one run, and `UpsetMonitor` follows that pattern. `CapabilityRegistry` and the optional provider facets in `capabilities/registry.py` give the monitor a way to reach a DAQ without naming one. `RunHandle.observing` already lists the instruments a run watches. The run's `EventBus`, `test.log` and run directory already carry results to the operator and to the archive. The DI-2008 driver gains a streaming mode. The current driver starts and stops a scan for each sample. It cannot see a short upset, and it cannot supply a pre-trigger window.

## Goals

- While a run is in flight, an operator sets a high limit, a low limit, or both, on any channel of a streaming DAQ. The operator does this on the run page.
- A reading outside a limit produces exactly one event for each excursion. The event holds `pre_s` seconds of scans before the crossing and `post_s` seconds after it, for every channel of that DAQ.
- Each event records the wall-clock time and the run time of the crossing. It also records the DAQ, the channel, the limit, and the reading that crossed the limit.
- The monitor saves each event as artifacts in the run directory, appends it to `test.log`, and publishes it on the run's event bus.
- The run page flashes when an event arrives, and lists the run's events. Selecting one draws its captured window.
- The DI-2008 streams at the rate its scan list allows: `clock / (srate * dec * channels)`, which `scan_rate_hz()` already reports. Work package 1 measured it at `srate 4`: 2000 scans per second for 1 channel, 50 for 4 and 25 for 8, with no scans lost. The stream uses the driver's scan rate, which follows the scan list it holds. That rate is `auto`, the fastest the enabled channels allow, unless an operator sets a slower one with the `scan_rate` command: 25 scans per second for 8 channels, 50 for 4 and 2000 for 1.
- The monitor records every event during the run. It has no cap on the number of events.
- An operator sets `stop_after`, the number of events after which the run stops. A value of 1 stops the run after the first event. A value of 0 never stops the run. The default is 0.
- A threshold change, and a `stop_after` change, applies within 1 second. The run does not restart.
- Preserve the behaviour of `configure` and `sample` on `Di2008Daq` for every suite that calls them today.
- A run whose suite never heard of the monitor behaves as before. The monitor sends nothing to the suite process.

## Non-Goals

- Detection inside a suite. The monitor is Gauntlet's, the same as `InstrumentRecorder`, and no suite declares or reads it.
- Streaming for `NiDaqmxDaq` or `MockDaq`. The facet is generic, but this plan adds it to `Di2008Daq` only.
- Thresholds that persist across runs, thresholds in a profile, and thresholds in a suite manifest.
- Rate-of-change, window, or pattern triggers. A limit is an absolute value.
- Changing the verdict of a run because of an event. An event does not affect pass or fail, the same as an `anomaly` record. `stop_after` ends the run early and leaves the verdict to the suite.
- Putting every scan on the event bus. The bus ring holds 5000 events, and a scan stream would push the log out of it.
- A new database table or column. Events live in the run directory only.
- Editing `extras/trl-ui-kit`.

## Current Acquisition Contract

`Di2008Daq` (`instruments/di2008_daq.py`) runs one scan per request. `_acquire` drains the endpoint, writes `start`, reads at least two scans, and writes `stop` in a `finally` block. One scan costs about 0.18 s. `state()` refreshes a reading older than `sample_interval_s` (1.0 s) through `_acquire`. `configure` reloads the scan list through `_configure`, which begins with `stop`. All of it runs under one `RLock`, so a panel poll, a suite `sample` and a `configure` take turns.

No provider publishes readings between requests. Nothing watches a DAQ between two samples. `InstrumentRecorder` reads `state()` once per second. A suite reads a DAQ through `POST /api/capabilities/{key}`, and the operator reads it through `GET /api/instruments`.

`RunRequest.observe` adds instruments to a run, and `Supervisor._observed(requires, observe)` joins them with the manifest's `requires`. The result is `RunHandle.observing`. A run's `EventBus` carries these types: `anomaly`, `end`, `iteration`, `log`, `metrics`, `phase`, `status` and `verdict`. `GET /api/runs/{id}/events` streams them to the run page through `useEventStream`. `Supervisor._announce` appends a timestamped line to `test.log` and publishes a `log` event.

A run directory holds the files listed in section 3 of `docs/contract.md`. `gauntlet.transfer.export_run` archives every file under the run directory. `RunsIndex.import_tree` rebuilds the runs table from those files.

## User Model

The operator opens a run that is in flight. A tab named **Upsets** appears on the run page when the run observes at least one streaming DAQ. The tab holds three parts.

- **Live trace.** One line per channel of the selected DAQ, showing the last 30 seconds, drawn from the streaming buffer. A DAQ selector appears when the run observes more than one.
- **Thresholds.** One row per channel with a **High** and a **Low** entry, both empty by default. Two entries, **Before** and **After**, set `pre_s` and `post_s` in seconds. One entry, **Stop after**, sets `stop_after`. **Apply** sends all of them. A channel with both limits empty is not watched.
- **Events.** A list, newest first. Each row shows the event number, the wall-clock time, the run time, the channel, the limit and the reading. Selecting a row draws the captured window, with the crossing marked at time zero.

When an event arrives, the tab title shows a count badge, and the tab flashes for 1.5 seconds. The trace pane border flashes at the same time. Both flashes use a CSS animation, and neither needs the tab to be open. The flash respects `prefers-reduced-motion` by showing a steady highlight instead.

| Entry | Range | Default | Behaviour outside the range |
|---|---|---|---|
| High, Low | any finite number, in the channel's unit | empty | A non-numeric value is refused with 422. |
| Stop after (`stop_after`) | 0 to 1000 events, whole number | 0 (never stop) | The API refuses the value with 422. |
| Before (`pre_s`) | 0 to 30 s | 2 s | The API refuses the value with 422. |
| After (`post_s`) | 0 to 30 s | 2 s | The API refuses the value with 422. |

The **Stop after** entry shows the count of events so far, as "2 of 5", when `stop_after` is above 0. When the monitor stops the run, the run page shows the reason "Stopped after 5 events" beside the status.

The run page shows nothing when a run observes no streaming DAQ. It does not explain why, because the tab is simply absent.

## Implementation

### Streaming capability

`capabilities/registry.py` adds an optional facet, `StreamingCapability`, with one method: `stream_since(seq: int, limit: int) -> StreamSlice`. `StreamSlice` holds `channels` (a list of `{key, label, unit}`), `rate_hz`, `next_seq`, and `scans`. Each scan is `(seq, monotonic_s, wall_s, values)`, where `values` lists one float per channel in `channels` order, and `None` for a channel with no reading. `seq` starts at 1 and rises by 1 per scan for the life of the provider. A caller passes the `next_seq` it last received.

`Di2008Daq` implements the facet. It starts a reader thread when a caller asks for streaming, and it keeps a ring of the newest scans. The ring holds 35 seconds of scans, which covers `pre_s` at its maximum plus a margin. The thread keeps the scan running and reads the bulk-IN endpoint in 64-byte packets. It decodes them with `decode_scans` and stamps each scan with `time.monotonic()` at the read that completed it, and with `time.time()`.

### Interaction with `configure` and `sample`

While streaming, `sample` and `state()` return the newest buffered scan and do not restart the device. `configure` stops the stream, reloads the scan list through `_configure`, and starts the stream again. The reader thread and the request path share the existing `RLock`. The thread releases the lock between packet reads. A command waits at most one read timeout (150 ms).

The driver streams only while at least one caller holds a stream lease. `stream_open()` and `stream_close()` count the leases. The last `stream_close()` sends `stop` and joins the thread. The driver then returns to its per-request behaviour. A run that never leases a stream sees no change.

### The monitor

`UpsetMonitor` lives in `supervisor/upsets.py`. The supervisor creates it in `_spawn` after `InstrumentRecorder`, starts it, and stops it in `_finalize`. It takes the registry, `RunHandle.observing`, the run directory, the run's `EventBus`, a callback that appends to `test.log`, and a callback that stops the run. The stop callback calls `Supervisor.stop` for the run through the event loop, so the stop is graceful. It follows every key in `observing` whose provider is a `StreamingCapability`. It opens one stream lease for each key. It knows no instrument name.

The monitor runs one thread. Every 50 ms, the thread calls `stream_since` for each followed DAQ and compares each new scan with the thresholds for that DAQ. A crossing follows these steps:

1. The first scan outside a limit, while the channel is armed, starts an event. The channel becomes disarmed.
2. The monitor copies the scans from `pre_s` before the crossing up to the crossing. It reads them from the ring. If the ring holds fewer scans, the monitor copies the scans that exist.
3. The monitor collects scans until `post_s` passes on the monotonic clock.
4. The monitor writes `upsets/upset_NNNN.csv` and rewrites `upsets.json`. It writes each file to a temporary name and then renames it.
5. The monitor publishes an `upset` event on the bus and appends one line to `test.log` through the callback.
6. The channel rearms when a reading is inside its limits and the post window of its event has ended.
7. When `stop_after` is above 0 and the number of finished events equals `stop_after`, the monitor calls its stop callback once. The count includes events on every followed DAQ.

The monitor counts an event when it finishes writing it, so the last event has its full post window before the run stops. A second channel that crosses during the window of the first event starts its own event. Each event keeps its own window. The windows overlap in time.

### Artifacts

`upsets/upset_NNNN.csv` starts with the header `t_s`, then one column per channel in the DAQ's label order. `t_s` is the scan time minus the crossing time, in seconds, negative before the crossing. A missing reading is an empty cell. `NNNN` is the event number, starting at 0001 and unique within the run.

`upsets.json` holds the current thresholds, `stop_after`, the list of events, and `stopped_run`. `stopped_run` is `true` when the monitor stopped the run and `false` otherwise. Each event has these fields: `index`, `file`, `instrument` (the instance key), `instance_id`, `channel`, `label`, `unit`, `direction` (`high` or `low`), `limit`, `value`, `at`, `elapsed_s`, `pre_s` and `post_s`. `at` is a UTC time in ISO 8601 with milliseconds. `elapsed_s` is the run time. The monitor rewrites `upsets.json` after each threshold change and each event. An interrupted run keeps the events that the monitor already wrote.

### Event log

The line in `test.log` reads `<UTC time> WARN upset #<index> <instance key> <label> <value><unit> crossed <direction> limit <limit><unit>`. The bus event has the type `upset` and carries the same fields as the `upsets.json` entry. `Supervisor._announce` is not reused, because it publishes a `log` event and the `upset` type needs its own shape.

### API

All routes belong to `api/runs.py`, and each answers 404 for an unknown run.

| Route | Answer |
|---|---|
| `GET /api/runs/{id}/upsets` | The thresholds, and the events recorded so far. Reads `upsets.json`, so it works for a finished or imported run. |
| `PUT /api/runs/{id}/upsets/thresholds` | Sets the thresholds for one DAQ, and `stop_after` for the run. Refused with 409 when the run is not in flight. Refused with 422 for an unknown instance key, an unknown channel, a non-finite limit, a `pre_s` or `post_s` outside 0 to 30, or a `stop_after` that is not a whole number from 0 to 1000. |
| `GET /api/runs/{id}/upsets/trace?instrument=<key>&since=<seq>` | The scans after `since`, at most 2000, with `channels`, `rate_hz` and `next_seq`. Refused with 409 when the run is not in flight. |
| `GET /api/runs/{id}/upsets/{index}` | The event's CSV, through the artifact path checks that already serve `upsets/` files. |

The trace route decimates nothing. The viewer polls it at 5 Hz, so a response holds 5 scans for 8 channels and 400 for 1 channel, under the 2000 limit.

### Cross-boundary statement

The change crosses `capabilities/` (the facet), `instruments/` (the DI-2008 driver), `supervisor/` (the monitor and its wiring), `api/runs.py` and `frontend/src/api/types.ts` with its captured fixtures. It does not cross `gauntlet_sdk.contract`: the new files are written by Gauntlet, the same as `instruments.jsonl`, and no suite declares or reads them. It does not cross `storage/`. `docs/contract.md` section 3 gains the rows for `upsets.json` and `upsets/`. `frontend/src/api/client.ts` gains the three request functions, and it stays the only module that calls `fetch`.

### Concurrency

The reader thread writes the ring, and the monitor thread and API requests read it, all under the driver's `RLock`. The monitor is the only writer of `upsets.json` and `upsets/`. A threshold request passes its values to the monitor through a dictionary that a lock guards. The monitor reads the dictionary on its next 50 ms pass. The bus is written with `publish_threadsafe`, the same as the metrics reader.

### Failure semantics

| Situation | Behaviour |
|---|---|
| The DAQ stops answering during a run | The reader thread ends the stream and records the reason in `unavailable_reason`. The monitor logs one `WARN` line to `test.log`, then retries a lease every 3 seconds. An event whose post window has not finished is written with the scans it has. `upsets.json` marks it `"truncated": true`. |
| The run ends during a post window | `_finalize` stops the monitor, which writes the open event as truncated before it returns. |
| A write to `upsets/` fails | The monitor logs the error to `test.log`, drops that event, and keeps watching. No partial file remains, because each file is written through a temporary name. |
| `configure` runs during a post window | The scan list changes, so the monitor ends the open events as truncated. |
| The run is aborted | The monitor stops with the run. Events already written stay. |
| `Supervisor.stop` refuses the call, because the run already ends | The monitor logs one `INFO` line and takes no other action. |

### Untrusted input

Threshold requests carry an instance key, a channel name and numbers. The API accepts an instance key only when it is one the run observes and its provider is streaming. It accepts a channel only when the provider lists it. Event index values in the download route must be an integer that exists in `upsets.json`. The file path comes from that entry and not from the request, so a request cannot name another path.

### Persistence and reimport

`upsets.json` and `upsets/` sit in the run directory. `RunsIndex.import_tree` needs nothing from them, and `gauntlet.transfer` archives them with the run. A run that ended before this change has neither. `GET /api/runs/{id}/upsets` answers an empty threshold set and an empty event list for that run. The monitor writes thresholds to the run directory only. They do not reach the data directory, and the next run does not inherit them.

## Design Decisions

### The trace is polled and events are pushed

Events are rare and small, so they go on the bus and reach late subscribers through the replay. Scans are frequent, so they use a cursor and a poll. The bus ring holds 5000 events, and a stream at 5 batches per second would push out every log line in about 17 minutes.

### One event per excursion

A channel disarms at the crossing and rearms when the reading is back inside its limits and the post window has ended. A noisy signal that sits on the limit therefore gives one event and not one per scan.

### Stopping the run

The stop is graceful. It uses `Supervisor.stop`, the same call as the Stop button. A suite that ends after a graceful stop records `passed` when every iteration passed, so the verdict stays the suite's own. The reason for the stop goes to `test.log` as one `INFO` line, and `upsets.json` sets `stopped_run` to `true`. The operator sees the reason on the run page. An operator who stops the run first, or a run that ends by itself, leaves `stopped_run` `false`.

### The monitor watches a DAQ the run already observes

`RunHandle.observing` is the run's list of instruments. It holds `requires` and the operator's `observe` list. A suite with `requires: []`, such as `daq_select`, is watched when the operator adds its DAQs to `observe`. This reuses the ownership rules and the panel lock that already exist for observed instruments.

### Timestamps

- `monotonic_s` orders scans and sets the pre and post windows. It never goes backward.
- `wall_s` is the UTC time shown to the operator.
- `elapsed_s` in an event is the time since the run's start, measured on the monotonic clock.
- The scan time is the time the read completed. It is later than the time of the sample by up to one packet, which is 45 ms at most, the longest gap measured in work package 1. The error is well under one scan period for 4 and 8 channels.

## Work Packages

### 1. Characterise the DI-2008 stream

The behaviour of a continuous scan on the real DAQ is not known. This package measures it and writes the result to `docs/instruments.md`. It changes no product code. The steps are:

1. Write a script in `tools/bench/`. The script opens one DI-2008 with `open_usb` and loads a scan list of 1, 4 and 8 channels. For each list, it starts a scan and reads for 60 seconds. Run the script against both DAQs on blinky.
2. For each list length, record the delivered scan rate, the largest gap between packets, the number of scans lost, and the bytes read per second.
3. Send `info 9` and then `stop` while a scan runs. Record whether the device answers and whether the stream stops. The result shows whether `configure` can run without a full drain.
4. Confirm that two DAQs stream at the same time on one host.

The artifact is a table in `docs/instruments.md`, in the section "The acquisition unit that is really two devices". Later packages use its rate figures. Gate: the table exists, and each delivered rate is within 1% of the rate the unit claims. Result: met. The 8-channel rate is 25 scans per second, and the user chose to accept it.

### 2. Streaming in the driver

Files: `capabilities/registry.py`, `capabilities/__init__.py`, `instruments/di2008_daq.py`, `packages/gauntlet/tests/test_instruments_real.py`.

1. Add `StreamingCapability` and `StreamSlice`.
2. Add the reader thread, the ring, and `stream_open`, `stream_close` and `stream_since` to `Di2008Daq`.
3. Make `sample`, `state()` and `configure` follow the rules in "Interaction with `configure` and `sample`".
4. Extend the fake transport in `test_instruments_real.py` so a test can feed packets from a generator.

The operator sees no change, because nothing leases a stream. The gate: `make gauntlet-test`.

### 3. The monitor and its wiring

Files: `supervisor/upsets.py`, `supervisor/supervisor.py`, `supervisor/__init__.py`, `packages/gauntlet/tests/test_upsets.py`, `docs/contract.md`.

1. Write `UpsetMonitor` as described in "The monitor".
2. Create it in `Supervisor._spawn` and stop it in `_finalize`, next to the recorder.
3. Add the `upsets.json` and `upsets/` rows to `docs/contract.md`.
4. Read `gauntlet.conformance`. Find out if its undeclared-artifact check refuses a file that Gauntlet writes, such as `instruments.jsonl`. If it does, add both new names to the files that it accepts. Add a test with a suite that fails the check.

The operator sees no change until the API package lands. The gate: `make gauntlet-test` and `make suite-verify-run`.

### 4. API

Files: `api/runs.py`, `packages/gauntlet/tests/test_api.py`, `docs/architecture.md`, `docs/instruments.md`.

1. Add the four routes in "API", with the validation rules in "Untrusted input".
2. Add the `upset` event to the event table in `docs/contract.md` as a Gauntlet-published type.
3. Add exact-shape assertions for each response body.

The gate: `make check`.

### 5. Frontend

Files: `frontend/src/api/types.ts`, `frontend/src/api/client.ts`, `frontend/src/hooks/useEventStream.ts`, `frontend/src/pages/RunPage.tsx`, `frontend/src/components/UpsetViewer.tsx` and `.scss`, `frontend/src/test/fixtures.ts`, and a test beside each changed file.

1. Add `RunUpsetEvent` to `types.ts` and `"upset"` to `EVENT_TYPES` in `useEventStream`. Add `getUpsets`, `putUpsetThresholds` and `getUpsetTrace` to `client.ts`.
2. Build `UpsetViewer` with a `recharts` `LineChart` and `parseCapture` and `decimate` from `utils/capture.ts`. The trace poll uses React Query with `refetchInterval` of 200 ms, and it stops when the run ends. The component names no instrument. It builds its rows from the channels the trace response lists.
3. Add the **Upsets** tab to `RunPage`, shown when `GET /api/runs/{id}/upsets/trace` answers 200 for an observed DAQ.
4. Write the flash as a CSS animation in `UpsetViewer.scss`. Add a `prefers-reduced-motion` variant. Use only colours from `@trl11/styles/theme.scss`.
5. Capture fixtures for the new responses, each assigned to its declared type.

The gate: `make frontend-check`, then `npm run screenshots`.

### 6. Bench validation

No code changes. The bench matrix in the test plan runs on blinky, after `make app-build` and `make deploy BENCH=trl@blinky`. The gate: every row of the matrix passes.

## Test Plan

### Unit and Component Tests

- Assert `Di2008Daq.sample` returns the newest buffered scan while a lease is open, and starts and stops a scan as before when none is open.
- Assert `configure` during a lease restarts the stream and the sequence numbers keep rising.
- Assert `stream_since(seq)` returns only scans after `seq`, and clips to the oldest scan when `seq` is older than the ring.
- Assert a reading above `high` produces one event whose CSV holds scans from `pre_s` before to `post_s` after, with `t_s` negative before the crossing.
- Assert a signal that stays above the limit for 100 scans produces one event.
- Assert `stream_since` reports `rate_hz` equal to `scan_rate_hz()`, and that it changes when `configure` changes the channel count.
- Assert a channel rearms when the reading returns inside the limits, and a second excursion produces a second event.
- Assert two channels that cross inside one window produce two events with overlapping windows.
- Assert a crossing 0.5 s after the start, with `pre_s` of 2, gives a window that starts at the first scan. Assert that the window has no padding.
- Assert a run that ends during a post window writes the event with `"truncated": true`.
- Assert a DAQ that stops answering logs one `WARN` line and does not end the run.
- Assert a failed write leaves no partial CSV. Assert that the monitor continues to watch.
- Assert `PUT .../thresholds` answers 422 for `pre_s` of 31, an unknown channel, an unknown instance key, and a non-finite limit. Assert it answers 409 for a finished run.
- Assert `GET .../upsets` answers empty lists for a run made before this change.
- Assert the monitor records 600 events on a run with `stop_after` of 0, and records all of them.
- Assert `stop_after` of 1 calls the stop callback once, after the first event finishes its post window, and not before.
- Assert `stop_after` of 3 with events on two DAQs counts events from both, and calls the stop callback once.
- Assert `stop_after` of 0 never calls the stop callback.
- Assert a change of `stop_after` during a run applies on the next pass.
- Assert a run stopped by the monitor has `stopped_run` of `true` and one `INFO` line in `test.log`, and that its verdict comes from the suite.
- Assert a `stop_after` of 1001, -1 and 1.5 gets 422.
- Assert an archive made with `export_run` after an event carries `upsets.json` and `upsets/`, and `import_run` restores them.
- Assert `RunPage` shows the **Upsets** tab only when the trace route answers 200.
- Assert an `upset` event adds a row, raises the badge, and adds the flash class, and that the class is absent under `prefers-reduced-motion`.

### Bench Matrix

Run on blinky with both DI-2008s registered as `daq.0` and `daq.1`. Save the run directory of each row.

| Row | Setup | Confirm |
|---|---|---|
| 1 | `daq_select` with `mock` driver off, `observe` set to `daq.0`, high limit 2.0 V on the channel wired to a 3.3 V rail, a 1 kΩ load switched to pull it below | One event, a CSV whose scans surround the crossing, and a flash in the browser. |
| 2 | Same, with a low limit 3.0 V and the rail switched off for 100 ms | One event with `direction: low`. Record whether the 100 ms drop is visible at the measured rate. |
| 3 | Both DAQs observed, limits on both | Separate events with the correct `instrument` and `instance_id` in each. |
| 4 | Unplug `daq.1` during a post window | The event is written truncated, `test.log` has one `WARN`, and `daq.0` keeps watching. |
| 5 | Change a limit during a run | The new limit applies within 1 second, with no restart. |
| 6 | Set `stop_after` to 1, then cause one excursion | The run stops after the post window, the CSV is complete, `stopped_run` is `true`, and the run page shows the reason. |
| 7 | Set `stop_after` to 0 and cause 10 excursions | All 10 events exist, and the run does not stop. |

### Contract Conformance

- Run `make suite-verify-run`. The run directory of every conformance profile must pass the undeclared-artifact check with the monitor active.

### Runtime Reconfiguration

1. Start a run with a streaming DAQ observed and no thresholds. The tab shows the trace and no events.
2. Set a high limit below the current reading. An event appears within the window plus 1 second.
3. Clear the limit. No further events appear, and the recorded events stay.
4. Stop the run. The tab keeps the events, and the trace stops updating.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| The scan rate is low for a long channel list, so a short upset can fall between two scans. | The rate is 25 scans per second for 8 channels, measured with no loss. The scan rate is 25 scans per second with all eight channels enabled. An operator who needs a finer trace disables channels: 50 scans per second for 4, 2000 for 1. The bench matrix, row 2, records whether a 100 ms drop is visible. |
| A continuous scan changes how `configure` and `sample` behave for existing suites. | The stream runs only under a lease. With no lease the driver keeps its current behaviour, and tests assert it. |
| The reader thread holds the lock and delays a suite command. | The thread releases the lock between reads. A command waits at most one 150 ms read timeout. |
| A long run on a noisy signal writes many events, because the monitor has no cap. | One event for each excursion, and a rearm rule. At `pre_s` and `post_s` of 2 s and 25 scans per second, one event holds 100 scans of 8 channels, which is about 8 KB. At 2000 scans per second for 1 channel, one event holds 8000 scans, which is about 100 KB. 10000 events use about 300 MB. The operator sets `stop_after` to end a run early. |
| The DAQ heats or degrades when it scans without a stop for hours. | Work package 6, row 1 runs for 30 minutes and records the errors that the DAQ reports. |
| The monitor rewrites `upsets.json` after every event, and the file grows with the event count. | The monitor rewrites the file at most once each second. It writes every event file at once, and the rewrite after the last event of a run is never skipped. |
| The bus ring holds 5000 events, so a run with more events loses the oldest from the replay. | The run page reads the full list from `GET /api/runs/{id}/upsets` when it opens, and it uses the bus only for new events. |
| The viewer polls when nobody is watching. | The poll stops when the tab is hidden or the run ends. |

## Delivery Order

Work packages 1 to 6 land in order. Package 2 lands before 3, because the monitor needs the facet. Package 3 lands before 4, because the routes read the monitor's files. Package 5 lands last among the code packages, because it uses the routes and the event type. A commit after package 3 is safe for two reasons. No run leases a stream until the monitor starts one. No operator sees the monitor until package 5 lands.

## Acceptance Criteria

1. `docs/instruments.md` holds the stream measurements from work package 1, and each delivered rate is within 1% of the claimed rate.
2. A run that observes a streaming DAQ, with a high limit below the live reading, writes `upsets/upset_0001.csv` and `upsets.json` within `post_s` plus 1 second.
3. The CSV holds scans from `pre_s` before the crossing to `post_s` after it, and its `t_s` column is negative before the crossing.
4. `test.log` holds one `upset` line for the event, and the event bus published one `upset` event.
5. The run page shows the flash within 1 second of the event, and the event list holds a row for it.
6. A signal that stays outside the limit produces exactly one event.
7. With `stop_after` of 1, the run stops after the first event finishes, and `stopped_run` is `true`. With `stop_after` of 0, the monitor records every event and never stops the run.
8. A run of a suite that does not use the monitor produces the same files as before, plus no `upsets.json`.
9. `GET /api/runs/{id}/upsets` answers for a finished run and for an imported run.
10. `make check`, `make frontend-check` and `make suite-verify-run` pass, and `npm run screenshots` reports no console error and no 4xx.
11. Every row of the bench matrix passes on blinky.
