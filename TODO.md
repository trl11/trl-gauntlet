# TODO

Work that is known to be missing, and the caveats that go with what is here.

## Electron packaging

Not started. The frontend was written for it — hash routing, and every request
prefixed with `VITE_API_BASE` so the bundle can be loaded from `file://` and
pointed at a Gauntlet process on another origin — but nothing packages the two
together, and there is no launcher that starts the backend beside the shell.

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
