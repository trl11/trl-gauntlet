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
all nine built-in suites pass, history and artifacts survive a restart in the
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

Verified packaged, not just in `make app-dev`: the installed app runs its own
CPython, discovers the nine suites beside it, keeps state in `userData`, and
passes both a shell suite and a Python one — which is the real test of the
`sys.executable` constraint above. Quitting kills the backend's process group,
so a suite mid-run goes with it.

### Next

No icon work beyond `app/icons/icon.png`, rasterised from `favicon.svg` at
512×512. electron-builder wants a set of sizes for the best result.

`make app-runtime` fetches 402MB of CPython and installs into all of it. The
`install_only` tarball carries the test suite, static libraries and headers,
none of which a packaged app runs. Pruning them is most of the AppImage's
243MB.

Nothing builds either artifact on CI, and both are x86_64 only.

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

`psu`, `daq` and `chamber` are registered as `MockPsu`, `MockDaq` and
`MockChamber`, one class per module in `gauntlet/instruments/`, sharing the
command and argument helpers in `gauntlet/capabilities/declare.py` and the
noise generator in `instruments/simulation.py`.

Real drivers exist in `trl-xclops/lab/src/xcng_lab/instruments/`: `hm310t.py`,
`di2008.py`, `can.py`, `rs422.py`. Each belongs beside the mocks in
`gauntlet/instruments/`. To replace a mock, a driver must satisfy the
`CapabilityProvider` protocol in `gauntlet/capabilities/registry.py` —
`available()`, `describe()`, `instance_id()`, plus `read()` and `write()` for
the HTTP proxy. The operator panel additionally reads the optional `state()`,
`commands()` and `command()` facets, and shows
`describe()["unavailable_reason"]` when a driver reports itself unavailable.

The panel is generated from those declarations, so a real driver needs no
frontend change.

## Known gaps

- Every ported suite has been exercised only through its mock driver. The SSH,
  serial, CAN and MQTT paths are untested against hardware. `system_stats` is
  the exception: it reads the host it runs on.
- `ssd` provisioning (`profiles/bare-disk.yaml`) is likewise mock-only.
- Instrument state comes only from the three mocks, so the Instruments screen
  has never been driven against a provider that can fail or go offline
  mid-command.
- `_write_scratch_profile` leaves files under `<runs>/_scratch/`. Nothing prunes
  them.
