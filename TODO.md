# TODO

Work that is known to be missing, and the caveats that go with what is here.

## Two ways to ship: the app and the server

Both targets are the same backend and the same bundle. What differs is who
starts the server and where the hardware is. A container reaches CAN, serial or
USB only through `--device`, `--network host` or privileged mode, which is why
the desktop app exists at all.

Decided: a relocatable CPython from python-build-standalone rather than a
frozen binary, a port chosen at startup rather than a fixed one, Linux only,
and the built-in suites shipped read-only inside the bundle. The Electron
directory is `app/`, matching trl-forge.

**The constraint that decides the bundle.** `launcher.py:53` puts
`Path(sys.executable).parent` on a suite's `PATH`, so `python` in a manifest
resolves to the interpreter Gauntlet runs under. Suites are separate processes
that import `gauntlet_sdk`. Freeze the server with PyInstaller and
`sys.executable` becomes the frozen binary, no interpreter sits beside it, and
every suite breaks. Shipping a real Python keeps `suite_environment()`
untouched.

### Done

The API base is resolved at runtime. `client.ts` takes the first of
`window.gauntlet.apiBase`, `VITE_API_BASE`, then same origin; `vite-env.d.ts`
declares the shape the preload script will satisfy. A build-time value could
never name a port chosen at startup.

The server image is in `docker/`, built from the repository root because it
needs both packages, the frontend and the ui-kit. `.dockerignore` stays at the
root: Docker reads it from the root of the context, not from beside the
Dockerfile. The devcontainer gained docker-outside-of-docker, and exports
`GAUNTLET_HOST_WORKSPACE` because the daemon reached through the mounted socket
resolves a bind mount against the host rather than against the container.

The image is built and exercised, through both `make docker-run` and compose:
all the built-in suites of the time pass, history and artifacts survive a restart in the
`gauntlet-data` volume, a graceful stop still writes a verdict and an abort is
recorded as `error`. `docker-run` publishes on `DOCKER_PORT`, which is 7102 in
the devcontainer, because the socket in there reaches the host that already
publishes 7100 and 7101 for the devcontainer itself.

The desktop app is in `app/`. `main.ts` asks the kernel for a port, spawns the
bundled `gauntlet serve --host 127.0.0.1`, polls `/api/health`, and shows the
window. `preload.ts` exposes `apiBase` over `contextBridge`.
`electron-builder.json` produces an AppImage and a deb under
`com.trl11.gauntlet`, with the CPython runtime and the suites as
`extraResources`.

**The window loads the backend's own URL, not `loadFile`.** The wheel already
carries `web_dist` and serves it at `/`, which is how `make run` and the image
work, so the shell reuses that instead of packaging a second copy of the bundle
into the asar. It also settles the question this file used to flag as untested:
the renderer is same origin with the API, so `useEventStream` involves no CORS
at all. Confirmed in the real browser rather than jsdom — a live run delivered
129 `iteration`, 129 `metrics`, 129 `phase` and 131 `log` events. The cost is
that the window cannot paint the UI until the backend answers, so it shows a
dark placeholder first, and the backend's stderr if the wait ends badly.

**The backend is started as `python3 -m gauntlet`, never through the `gauntlet`
console script beside it.** pip writes that script an absolute shebang naming
the interpreter as it stood when `make app-runtime` ran, so on any machine but
the build host it fails to exec with `ENOENT` — reported against a file that
plainly exists, because it is the interpreter that is missing, not the script.
The interpreter is the one thing python-build-standalone makes relocatable, so
it is the one thing invoked by path.

Verifying this needs the build tree gone. An unpacked app tested on the machine
that built it finds `app/runtime/` still sitting at the shebang's path and
passes either way. Test by moving `app/runtime` aside and running the extracted
AppImage, which is how it was verified: the installed app runs its own CPython,
discovers the suites beside it, keeps state in `userData`, passes a shell
suite and two Python ones — the real test of the `sys.executable` constraint
above — and takes the backend's process group with it when it quits.

An AppImage needs libfuse2, which the devcontainer does not have and the server
image has no use for, so neither installs it. Run it with
`--appimage-extract-and-run` in there.

**The runtime is pruned after it is installed into.** The `install_only`
tarball is built to develop against, not to ship: `make app-runtime` drops the
headers, static libraries, `pkgconfig`, the test suite, `idlelib`, `lib2to3`
and `tkinter`, and strips the debug symbols from `libpython`, which alone were
five sixths of it. `bin/python3.12` is left unstripped because what comes out
cannot resolve its own symbols. `make app-smoke` runs the packaged app with
nothing to fall back on, which is what says a prune went too far. The AppImage
is 196MB.

### Next

No icon work beyond `app/icons/icon.png`, rasterised from `favicon.svg` at
512×512. electron-builder wants a set of sizes for the best result.

Nothing builds either artifact on CI, and both are x86_64 only.

## Campaigns

A campaign is a directory with a `campaign.yaml` that groups the suites of one
programme and contributes its own suite directory to discovery. It is not a
session: no start, no end, no state, and running a member records nothing about
it. Which campaign a suite belongs to is derived from where it sits on disk, so
a run names the campaign grouping its suite now rather than the one that
started it. See [`docs/campaigns.md`](docs/campaigns.md).

Two are built in: `hardware`, the suites that drive real hardware, and
`radiation_tid`, one suite per component of the TID programme.

**Some TID suites are still placeholders.** `tid_lan7430`, `tid_max96793` and
`tid_max96792` measure. The rest — `tid_ads7138`, `tid_asm330lhb`, `tid_tmp100`
and `tid_pic18f26k83` — render from the python template: they run, write a
verdict and pass without hardware, and measure nothing at all. Each `runner.py`
opens with the component, test vehicle, host and fixture it is for, and the
measurements it has to grow into, so a green `radiation_tid` says only that
those scaffolds still execute.

`tid_pic18f26k83` is TBD in the test matrix with no test vehicle or approach
agreed, and is scaffolded anyway so the gap is visible in the campaign. The
matrix also carries test-plan edits and BOM additions per component, recorded
in each runner's docstring and not tracked anywhere that would chase them.

### Next

Campaigns group runs; they do not sequence them. There is no way to run a
campaign, only a member of one, and nothing defines what finished means for a
programme. Deciding that is what a `target` in the manifest would be for.

`GET /api/runs` takes no campaign filter. The history table shows a campaign
column and can be searched by title, but the API cannot be asked for one
campaign's runs, so anything larger than a page has to filter client-side.

Nothing warns that a campaign key or a suite key collides across roots beyond
the error the catalog collects. Two campaigns shipping the same suite key is
reported and then the second is ignored.

## Suites not ported

These remain in `trl-xclops/testing/`:

| Suite | Blocked on |
|---|---|
| `rad_camera` | Probes cameras through the xclops SDK. |
| `burn_in`, `thermal`, `power_cycle`, `power_measurement`, `soak`, `api`, `ui` | The xclops SDK, controller, or frontend. |

Two behaviours from the source suites were not carried over and would need
bench time to add:

- The `lab_sender` mode of `rad_can`, which drives a Waveshare serial USB-CAN
  adapter from the lab host.
- The RS422 auto-detect fallback for adapters other than FTDI `0403:6001`.

The two ported suites were renamed from `rad_hardware_trigger` and `rad_ssd`.
The suites still to come carry the same prefix in the source repository.

## Real instrument drivers

`psu` and `daq` are real drivers — `Hm310tPsu`, Modbus RTU over a USB serial
bridge, and `Di2008Daq`, the vendor bulk-USB protocol through usbfs — beside
the mocks in `gauntlet/instruments/`. `detect.py` registers whichever answers
and drops whichever stops answering, and registers a simulation only when
`simulated_instruments` names it. `chamber` has no driver for real hardware, so
it exists only while it is simulated.

`can.py` and `rs422.py` in the lab checkout have no counterpart here, and do
not obviously want one: the `can_bus` and `rs422` suites declare `requires: []`
and drive their own hardware, so neither is an instrument the operator sees.

What a driver owes the rest of the system is in
[`docs/instruments.md`](docs/instruments.md). The panel is generated from a
provider's declarations, so a real driver needs no frontend change.

### What the bench has actually seen

The PSU on `/dev/ttyUSB0` was driven read-only on 2026-08-04: detection picks
it over `MockPsu`, `state()` reads its setpoints and display in ~75ms, and a
scan leaves the live connection alone. **Every PSU write is unverified.**
`set_voltage`, `set_current_limit` and `set_output` are tested against a fake
supply and have never been sent to the device, because enabling the output
energises whatever is wired to it. Ask before the first real write.

A DI-2008 was attached on 2026-08-06 — bus 003, serial `6A046A27`, firmware 76
— and the driver written blind against the protocol was wrong in three ways a
fake transport could not have shown. All three are fixed and the unit now reads
through both the panel and a granted capability URL. What it has not seen is a
thermocouple against a known junction: the scaling path works and reads a
sensible magnitude, but only ever from an open input.

Its usbfs node is not writable until the host udev rule is installed, which
`make install-udev-rules` does and `make udev-check` reports on. **It is
installed on no machine**: this bench was unblocked by hand, which lasts until
the DAQ is replugged.

Two things the DAQ driver leaves out: the digital input bank (slist channel 8)
and the rate/count channels 9–10, only the eight analog inputs being in the
scan list, and averaging — a sample is the last complete scan in the window
rather than the mean of it.

Channel labels and modes live in the driver for the session. Nothing writes
them down, so a restart puts every channel back to `10v` under its number. A
`daq_capture` run sets both from its profile at setup, so a run configures the
bench it is about to measure and does not depend on what the last one left.

`daq_capture` is what proved the whole path on hardware: the supervisor grants
the capability, the suite configures eight channels in one call, scans them for
the duration, and each scan lands in `metrics.jsonl` under the channel's label,
so RunPage charts `daq.rail_3v3`. `GET /api/instruments` reports the run in
`in_use_by` while it is in flight.

## Known gaps

- Every ported suite has been exercised only through its mock driver. The SSH,
  serial, CAN and MQTT paths are untested against hardware. `system_stats` is
  the exception: it reads the host it runs on.
- `ssd` provisioning (`profiles/bare-disk.yaml`) is likewise mock-only.
- The Instruments screen has been driven against two real providers, the PSU
  read-only and the DAQ read and write. Nothing has exercised a provider that
  fails or goes offline mid-command.
- Only `daq_capture` drives a capability. Every other suite is `requires: []`
  and reads its own hardware, so an instrument they depend on is neither
  granted to them nor reported as in use while they run.
- `_write_scratch_profile` leaves files under `<runs>/_scratch/`. Nothing prunes
  them.
- A suite that moves has three places to follow it, and each was found only
  after it broke: `gauntlet.catalog.scan` for what the CLI and server discover,
  `SUITE_SOURCES` for what the quality targets read, and the packaging in
  `docker/Dockerfile` and `app/electron-builder.json` for what ships. Nothing
  ties them together, so a fourth would be missed the same way.
- The desktop app and the server image both name `campaigns/` now, so both
  carry every suite inside it. Neither has been rebuilt since the TID campaign
  was added, which is 18 more suites in both artifacts, so what sits in `dist/`
  is a bench without them.
