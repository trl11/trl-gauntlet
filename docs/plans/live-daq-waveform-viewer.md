# Live DAQ Waveform Viewer

Status: proposed

`InstrumentPanel` already gives a command that answers with a picture a live viewer: a Snapshot/Continuous toggle that self-paces a repeating command and draws whatever came back, generic over every instrument (`returns: "image"`, consumed today by the camera and logic-analyzer providers). This plan extends that same convention with a second kind, `returns: "waveform"`, and declares it on `NiDaqmxDaq`'s `capture` command, so the Instruments page gets a live, scrolling multi-channel chart of a DAQ module's analog inputs with no suite-specific or instrument-specific code anywhere. Reading a rail or a current shunt is what this is for: an operator watches the chart while something happens on the bench and reads a spike off it as it is captured, rather than only after a suite has written a CSV.

## Goals

- An operator on the Instruments page starts a live-updating chart of a `daq` module's analog channels, and stops it, without leaving that page.
- Each update is one capture window at a rate and a sample count the operator can see and change, high enough by default to show a spike that lasts a few hundred microseconds.
- The mechanism is the existing generic viewer convention (`returns`), widened by one value. No file under `frontend/src/` names `daq` or any other instrument outside a test fixture.
- The existing image viewer (camera, logic analyzer) keeps behaving exactly as it does today.

## Non-Goals

- Gapless, continuous acquisition. `NiDaqmxDaq.capture` opens a fresh `nidaqmx.Task`, blocks for the window, and closes it (`ni_daqmx.py`, `_capture`); there is no persistent acquisition handle and no chunked delivery. Closing the small gap between one capture and the next needs `AcquisitionType.CONTINUOUS` and a background reader thread in `_DaqmxModule`, a driver-level change this plan does not make.
- Support for `Di2008Daq` or `MockDaq`. Neither declares a `capture` command today (`di2008_daq.py`, `mock_daq.py` offer only `configure`/`sample`), and this plan does not add one.
- Recording what the live viewer shows. No capture it takes is written to a run directory or any other file; it is exactly as ephemeral as the existing image preview.
- A dedicated route or page per instrument. The viewer stays inside `InstrumentPanel` on the existing Instruments page.
- Converting a channel's voltage reading to a current. A shunt's scale factor is a bench fact carried in the channel's own `label`, the same as any other reading; this plan changes nothing about how a channel is labelled.
- Changing `initialValue`/`initialArgs` in `utils/commandFields.ts`, which every other command's plain numeric field still uses unchanged.

## Current Viewer Contract

`GET /api/instruments` and `POST /api/instruments/{key}/command` (`packages/gauntlet/src/gauntlet/api/instruments.py`) report a provider's `commands()` as plain dicts built by `capabilities/declare.py`'s `command_field`/`command_row` helpers. A command dict may carry `"returns": "image"`, set today on `alvium_camera.py`, `uvc_camera.py`, `fx2_logic.py`, and their mock counterparts. `frontend/src/api/types.ts`'s `InstrumentCommand.returns` is already typed as a bare `string`, so a new value needs no type change there, only an updated doc comment.

`InstrumentPanel.tsx` finds that command (`viewer`), takes it out of the deck (`others = instrument.commands.filter((command) => command !== viewer && ...)`), and draws a Snapshot/Continuous toggle plus any of the command's fields that declare `choices` (`presets`), seeded from each field's first choice. Snapshot sends the command once; Continuous sets `live` and a `useEffect` re-sends the command as soon as the previous result lands, passing `{...settings, live: true}` — self-pacing, with no polling interval to tune. The result's `image_base64` is read one level up, in `InstrumentsPage.tsx`'s `imageFrom()`, and stored as `shot: (InstrumentPreview & { name: string }) | null`, keyed by instrument name so a different instrument's command does not clear it. `InstrumentPreview` is `{ src: string }`; the panel renders it as `<img src={preview.src}>` beside a dismiss button.

Only a field with `choices` gets a control in this bar today. `NiDaqmxDaq`'s `capture` command (`ni_daqmx.py`, `commands()`) declares `rate_hz` and `samples` as plain numeric fields with `dial=False` and both a `minimum` and a `maximum`, no `choices` — so `presets` is empty for it, and `initialValue()`/`initialArgs()` (`utils/commandFields.ts`) would seed a plain, non-dialled numeric field to `""`, which `coerce()` turns into `Number("") === 0`. `_capture`'s `number_arg("daq", args, "rate_hz", slowest, fastest)` rejects `0` as outside `[slowest, fastest]`. Declaring `returns: "waveform"` on `capture` with no further change would make every request the live loop sends fail immediately.

## User Model

On the Instruments page, a `daq` instrument whose provider declares `capture` with `returns: "waveform"` gets the same Snapshot/Continuous bar the camera and logic analyzer already get, in place of `capture`'s card in the deck:

- Two number entries, one per non-choice field the command declares (`Sample rate`, `Samples`), each starting at that field's own `maximum` — 25000 samples at up to 50000 S/s on an NI-9238, so the panel opens already capturing at the finest resolution and the largest window the hardware and the 25000-sample API cap allow. The operator can lower either to trade window length for a livelier refresh, exactly as they could type any value into the deck's card before this change.
- **Snapshot** takes one capture and draws it.
- **Continuous** repeats: as soon as one capture is drawn, the next is asked for, so the chart scrolls to the newest window at whatever cadence a capture of that size takes plus the round trip.
- **Stop** (the same button, now reading "Stop") ends the loop; the last capture stays on screen until dismissed or replaced.
- The chart is one line per channel, its own `Line` colour and a toggle button per channel to hide it, matching `CaptureViewer`'s existing convention for a finished run's capture file. The x-axis is milliseconds from the start of the window; the y-axis is volts.

What does not appear: no history across captures (each capture replaces the last, the way each image replaces the last), no export, no unit conversion.

## Implementation

### Backend: declare the return kind

`ni_daqmx.py`'s `commands()` adds `"returns": "waveform"` to the `capture` command dict, with a one-line comment matching `fx2_logic.py`'s: the result is thousands of samples, so the panel draws them as a trace rather than listing what came back. Nothing else in `ni_daqmx.py` changes: `_capture()`'s response shape — `{"rate_hz", "samples", "window_s", "channels": {name: {"label", "unit", "mean", "min", "max", "peak_to_peak", "values"}}}` — is already what the viewer needs. A suite reaches `capture` through `POST /api/capabilities/{key}` (`api/capabilities.py`), a different router that never reads `commands()`, so no suite is affected by this change.

### Frontend: turn one capture result into a `Capture`

`frontend/src/utils/liveCapture.ts` (new) declares the wire shape of a capture command's result and adapts it into the `Capture` type `utils/capture.ts` already defines (`{ channels: string[], times: number[], values: number[][] }`), so `decimate()` and `CapturePoint` are reused rather than reimplemented:

```
export interface WaveformChannel {
  label: string;
  values: number[];
}
export interface WaveformResult {
  rate_hz: number;
  channels: Record<string, WaveformChannel>;
}
export function captureFrom(result: WaveformResult): Capture
```

`captureFrom` builds one column per entry of `result.channels`, in the order Object.entries returns them, naming each column the channel's own `label` when it is non-empty, else the channel's key. When a later channel's name (label or key) matches one already used, it falls back to its own key instead, so two channels sharing a label never overwrite one column with the other — the same collision this suite already resolves in `campaigns/hardware/suites/daqmx_capture/suite/runner.py`'s `_named()`. `times` is `index / rate_hz` for `index` from `0` to the longest channel's sample count minus one; a channel shorter than that (the module answering with fewer samples than asked) is padded with `undefined` past its own length, which `decimate()` already treats as absent — the same gap `parseCapture()` leaves for a dropped CSV row.

### Frontend: draw it

`frontend/src/components/WaveformViewer.tsx` and `.scss` (new) render a `Capture` the way `CaptureViewer.tsx` renders one it read from a file: a per-channel toggle row, a `recharts` `LineChart` with one `Line` per shown channel, `CartesianGrid`, an `XAxis` formatted in milliseconds, a `YAxis` `paddedDomain`-scaled to what is drawn, and a `ChartTooltip`. It takes one prop, `capture: Capture`, decimates with the same `BUDGET = 1200` `CaptureViewer` uses, and carries none of that component's file-fetching, iteration picker, or `Brush`/zoom state — a live capture is one window, replaced whole each time, not a file an operator scrubs through.

### Frontend: widen the viewer bar

`InstrumentPanel.tsx` changes in four places:

1. `InstrumentPreview` becomes a discriminated union: `{ kind: "image"; src: string } | { kind: "waveform"; capture: Capture }`.
2. `viewer` widens from `command.returns === "image"` to `command.returns === "image" || command.returns === "waveform"`.
3. `presets` widens from `field.choices.length > 0` to that, or a field with both `field.min !== null` and `field.max !== null` and no `choices` — a plain ranged numeric field. Each preset is drawn with `FieldControl` (`components/FieldControl.tsx`), already generic over exactly this distinction (`choices` gets a `Select`, a plain numeric field an `Input`, per its own `dialled()` check), in place of the hand-rolled `<Select>` the bar draws today. A local `initialPresetValue(field)` — `field.choices[0]` when there are choices, else `field.max` — seeds `settings` in the existing `useEffect` keyed on the preset fields' shape; this is a new, local function, not a change to the shared `initialValue()` every other command's form still uses.
4. Sending a command from the viewer — both the Snapshot button and the Continuous loop — coerces every preset through `coerce(field, value)` (`utils/commandFields.ts`, already used by `CommandForm` for this exact purpose) before it is sent, so `rate_hz` and `samples` cross the wire as JSON numbers rather than the numeral strings an `<Input>` holds.

Rendering `preview`: an `image` preview draws the existing `<img>`; a `waveform` preview draws `<WaveformViewer capture={preview.capture} />` inside the same `instrument-panel__shot` wrapper, so the dismiss button and the "the last image/capture this instrument answered with" framing stay shared.

### Frontend: extract a waveform result

`InstrumentsPage.tsx` adds `waveformFrom(result): Capture | null`, mirroring `imageFrom()`: it reads `result.rate_hz` and `result.channels`, returns `null` when either is absent or `channels` is empty, else `captureFrom(result as WaveformResult)`. `send`'s `onSuccess` tries `imageFrom` first, then `waveformFrom`, and tags `shot` with `kind: "image"` or `kind: "waveform"` accordingly.

No change crosses into `gauntlet_sdk.contract`, `storage/`, or any persisted column — nothing here is written to a run, a unit, or the index, so `RunsIndex.import_tree` and reimport are untouched. No new endpoint, path, upload, or address is accepted, so there is no new untrusted input to validate.

## Design Decisions

### Why the viewer bar, not a new panel

`InstrumentPanel` is the one instrument component (`docs/frontend.md`); a second, waveform-only panel would duplicate the Snapshot/Continuous state machine that already exists and works. Widening `returns` and `InstrumentPreview` is the smaller change and keeps the "one instrument component" rule intact.

### Default rate and sample count

Every preset field, choice or plain numeric, starts at a value the field itself declares (`choices[0]`, or `max`), never a value this plan invents. For `capture`, that is the module's fastest rate and the API's 25000-sample cap — the finest resolution and the largest window `_CAPTURE_LIMIT` allows, by construction, not by a number written into `InstrumentPanel.tsx`. A bench whose module runs slower, or a future provider with a different cap, gets its own correct default the same way, with no frontend change.

### Channel naming and collisions

A capture names each channel by its own `label` because that is what an operator wired it to mean — the same convention `CaptureViewer`'s CSV header and `daqmx_capture`'s `_named()` already use. Falling back to the channel's raw key on a collision, rather than silently dropping a duplicate, is what `_named()` does for the same reason: a channel nobody named `ai0` and a channel named `ai0` racing for one column would lose one of them.

## Work Packages

### 1. Backend: declare the waveform return

Add `"returns": "waveform"` to `capture`'s command dict in `ni_daqmx.py`, with the one-line comment about why (the result is drawn, not listed). Extend `packages/gauntlet/tests/test_ni_daqmx.py::test_the_capture_command_offers_the_module_s_own_rates` (or a new test beside it) to assert `commands()`'s `capture` entry carries `"returns": "waveform"`.

Exit gate: `make gauntlet-test`.

### 2. Frontend: the adapter and its fixture

1. Add `WaveformChannel`, `WaveformResult`, and `captureFrom()` to `frontend/src/utils/liveCapture.ts`, reusing `Capture`/`decimate` from `utils/capture.ts`.
2. Add a `daq` instrument to `frontend/src/test/fixtures/api.json`'s `instruments` array, its `capture` command carrying `"returns": "waveform"`, shaped like `NiDaqmxDaq.describe()`/`commands()`/`state()` actually answer (four `ai0`–`ai3` channels, `rate_hz`/`samples` fields with real `min`/`max`). Export it from `fixtures.ts` as `daqInstrument: Instrument`.
3. Unit-test `captureFrom()`: one channel's `label` used as its column name; an empty `label` falling back to its key; two channels sharing a label producing two distinct columns; `times` computed from `rate_hz`.

Exit gate: `make frontend-test`.

### 3. Frontend: `WaveformViewer`

Add `WaveformViewer.tsx`/`.scss`, modeled on `CaptureViewer.tsx`'s chart body (channel toggles, `LineChart`, axes, tooltip) with no file-fetching, iteration picker, or zoom state. `WaveformViewer.test.tsx` asserts: a channel's toggle hides its `Line`; a capture with one channel draws one line; an empty capture (`channels: []`) renders nothing rather than an empty chart shell — mirror `CaptureViewer.test.tsx`'s existing assertions for the parts both share.

Exit gate: `make frontend-test`.

### 4. Frontend: widen `InstrumentPanel`

1. Change `InstrumentPreview` to the two-member union.
2. Widen `viewer`'s predicate to include `"waveform"`.
3. Widen `presets` to include a plain ranged numeric field; add `initialPresetValue()`; replace the bar's `<Select>` with `<FieldControl>`.
4. Coerce every preset through `coerce()` before either send path.
5. Render `WaveformViewer` for a `waveform` preview, `<img>` unchanged for an `image` one.

Extend `InstrumentPanel.test.tsx` with a `describe("InstrumentPanel shows a waveform a command answered with")` block, mirroring the existing image block's cases against `daqInstrument`: offers the viewer before any capture has arrived; the field's own `max` is what Snapshot sends first; Continuous self-paces the same way the image loop does; a rejected command stops the loop; dismissing clears the chart; editing a preset's number entry changes what the next capture asks for.

Exit gate: `make frontend-test`.

### 5. Frontend: `InstrumentsPage`

Add `waveformFrom()`; widen `shot`'s type to `(InstrumentPreview & { name: string }) | null` using the new union; try `imageFrom` then `waveformFrom` in `send`'s `onSuccess`.

Exit gate: `make frontend-check`.

### 6. Docs

Add a section to `docs/frontend.md` documenting the generic viewer convention this plan extends: a command declaring `returns: "image"` or `returns: "waveform"` is taken out of the deck and drawn as a live Snapshot/Continuous view, and no frontend file may special-case which instrument or command this applies to. Add a line to `docs/instruments.md`'s existing description of `capture` (`"Reading a waveform rather than a level"` section) pointing at that convention.

Exit gate: none beyond review; docs carry no test.

## Test Plan

### Unit and Component Tests

- `captureFrom()`: label used as column name; empty label falls back to key; a label collision keeps both columns, the second under its raw key; `times` matches `index / rate_hz`.
- `WaveformViewer`: hides a channel's line on toggle; an empty capture renders no chart.
- `InstrumentPanel`, `daq` fixture:
  - the viewer appears before any capture has been taken, with `rate_hz` and `samples` entries pre-filled to the field's own `max`;
  - Snapshot mode sends exactly one command, carrying the pre-filled values coerced to numbers;
  - Continuous mode sends the next command only once the previous result has arrived (mirrors the existing image test of the same name);
  - a rejected command stops Continuous mode, same as the image case;
  - dismissing removes the chart and leaves the fields as they were;
  - typing a new value into the `samples` entry changes the argument the next Snapshot or Continuous request carries.
- `InstrumentsPage`: a command result carrying both no `image_base64` and a `rate_hz`/`channels` pair is drawn as a waveform, not left blank.

### Backend Test

- `ni_daqmx.py`'s `capture` command reports `"returns": "waveform"` in `commands()`.

### Runtime Reconfiguration

1. Start Continuous mode on a simulated `daq` instrument (`simulated_instruments: [daq]`).
2. Lower `samples` mid-run.
3. The next capture the chart draws reflects the new count, and the loop keeps running rather than restarting or stalling.

## Acceptance Criteria

- A `daq` instrument whose provider declares `capture` with `returns: "waveform"` shows a Snapshot/Continuous chart on the Instruments page; `capture` no longer appears as a deck card for that instrument.
- The chart's default request uses the field's own `rate_hz` and `samples` maxima, with no value hardcoded in `frontend/src/`.
- The existing image viewer (camera, logic analyzer) is unchanged: its tests in `InstrumentPanel.test.tsx` pass unmodified.
- `grep -rn '"daq"' frontend/src` outside `frontend/src/test/` returns nothing.
- `make frontend-check` and `make gauntlet-test` pass.

## Risks and Mitigations

| Risk | Mitigation |
|---|---|
| A capture's dead time (task open/close, JSON encoding, the HTTP round trip) between one window and the next hides a spike that falls in the gap | Documented as a non-goal here; the default window (25000 samples at the module's fastest rate) makes the gap a small fraction of the window, and a real fix is a driver-level continuous-acquisition change, tracked separately |
| Widening `presets` to include plain numeric fields changes what any other command with `choices`-only presets renders | The predicate only adds fields with both `min` and `max` set and no `choices`; a field with neither stays out of the bar exactly as today |
| A capture whose channels do not all return the same sample count leaves ragged columns | `captureFrom` pads a shorter channel past its own length the same way `decimate()` already treats a missing value, rather than truncating every column to the shortest |

## Delivery Order

1. Backend: declare `returns: "waveform"` (Work Package 1).
2. Frontend adapter and fixture (Work Package 2), landable and testable with no visible change yet — nothing renders it.
3. `WaveformViewer` (Work Package 3), testable standalone.
4. `InstrumentPanel` widened (Work Package 4) — this is what makes the daq fixture's viewer appear; land after 1–3 so its tests have both a real `returns: "waveform"` command and a working chart to render.
5. `InstrumentsPage` wiring (Work Package 5) — the last piece a real bench needs; before this lands, a live `daq` capture answers but nothing on the page shows it.
6. Docs (Work Package 6).
7. Deploy to a bench carrying a real NI-9238 and confirm Continuous mode against real hardware, since every package before this ran against the mock and the fixture only.
