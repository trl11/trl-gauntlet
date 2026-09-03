# TODO

Work that is known to be missing, and the caveats that go with what is here.
What each of the three ship targets is and why it is built that way is in
[`targets/README.md`](targets/README.md) and the three beside it.

## Shipping

No icon work beyond `targets/app/icons/icon.png`, rasterised from `favicon.svg`
at 512×512. electron-builder wants a set of sizes for the best result.

Every artifact is x86_64 only. The runtime's `PYTHON_BUILD_TARGET` and the rig
deb's `Architecture` both say so, and nothing cross-builds.

## Campaigns

Campaigns group runs; they do not sequence them. There is no way to run a
campaign, only a member of one, and nothing defines what finished means for a
programme. Deciding that is what a `target` in the manifest would be for.

`GET /api/runs` takes no campaign filter. The history table shows a campaign
column and can be searched by title, and a run carries the campaign grouping
its suite, but the API cannot be asked for one campaign's runs — so anything
larger than a page has to filter client-side.

Nothing warns that a campaign key or a suite key collides across roots beyond
the error the catalog collects. Two campaigns shipping the same suite key is
reported and then the second is ignored.

**Some TID suites are still placeholders.** `tid_lan7430`, `tid_max96793`,
`tid_max96792` and `tid_ads7138` measure. The rest — `tid_asm330lhb`,
`tid_tmp100` and `tid_pic18f26k83` — render from the python template: they run,
write a verdict and pass without hardware, and measure nothing at all. Each
`runner.py` opens with the component, test vehicle, host and fixture it is for,
and the measurements it has to grow into, so a green `radiation_tid` says only
that those scaffolds still execute.

`tid_pic18f26k83` is TBD in the test matrix with no test vehicle or approach
agreed, and is scaffolded anyway so the gap is visible in the campaign. The
matrix also carries test-plan edits and BOM additions per component, recorded
in each runner's docstring and not tracked anywhere that would chase them.

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
the DAQ is replugged. Installing the rig deb is the other way to get it there.

Two things the DAQ driver leaves out: the digital input bank (slist channel 8)
and the rate/count channels 9–10, only the eight analog inputs being in the
scan list, and averaging — a sample is the last complete scan in the window
rather than the mean of it.

Channel labels and modes live in the driver for the session. Nothing writes
them down, so a restart puts every channel back to `10v` under its number. A
`daq_capture` run sets both from its profile at setup, so a run configures the
bench it is about to measure and does not depend on what the last one left.

## Known gaps

- Every ported suite has been exercised only through its mock driver. The SSH,
  serial, CAN and MQTT paths are untested against hardware. `system_stats` is
  the exception: it reads the host it runs on.
- `tid_ssd` provisioning is likewise mock-only, and no profile enables it: the
  block formats and mounts a bare disk, which is destructive and has never run
  against one. `ssd` dropped it when it became a quick check.
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
  `SUITE_SOURCES` for what the quality targets read, and the packaging in each
  of the three ship targets. Every campaign travels as a directory now, which
  is fewer paths to miss than when suites were named too, but nothing ties the
  three together and a fourth would be missed the same way.
- The rig deb has been built and its contents checked, but never installed on a
  bench. `postinst`, the two user units and the landing page on port 80 have
  not run under dpkg on real hardware.
