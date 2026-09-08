# tid_pic18f26k83 — the test, the bench, and what is still open

This file is the design: what the measurement should be, what has to be on the
bench for it, and which parts of it nobody has confirmed yet. One of the seven
measurements below is implemented, because one is what today's bench can take —
see [What runs today](#what-runs-today).

The component is the `PIC18F26K83-E/SS` that runs PMU3, the power management
unit between raw input power and the Jetson. Its firmware is a submodule at
[`extras/trl-pmu-firmware`](../../../../extras/trl-pmu-firmware/), and the
whole of this test is built on the fact that the part already carries an
interface designed to be read from a computer:
[`docs/reference/jetson-i2c.md`](../../../../extras/trl-pmu-firmware/docs/reference/jetson-i2c.md)
is an I2C client at `0x28` with telemetry, reset history and a fault latch.
Gauntlet's `i2c` capability is a CP2112 driving `/dev/i2c-N` through
`I2C_RDWR`, which is exactly what `pmu3ctl` already talks to a board with.

## What runs today

The programmer and the debug UART, and nothing else. That is enough for the
first measurement below, which is implemented in `suite/`, and not enough for
the six after it, which are not.

It is more than it sounds. The firmware carries a telemetry stream written for
a radiation campaign — a parseable frame a host can timestamp, and a liveness
beat whose *absence* is the measurement — so the cheapest bench there is
already reaches the part's own detectors, its reset history, its firmware
build and its time base. The instruments buy the analog parameters and the
sequencer, not the basic characterisation.

The programmer's job is over before the run starts: it flashes the image, and
ICSP asserts the part's reset pin, so nothing drives it while a run is in
flight. The image identity comes off the wire instead — every `@B` frame
carries `fw=`, which is better provenance than a device id and costs nothing.

Run `bench.yaml` against a board to see all of it working.

## Programming the part

Nothing here programs a part during a run, and the suite has no way to. A part
is flashed before a campaign; a run reads one that is already executing.

The image is committed at [`firmware/pmu3_firmware.hex`](firmware/) beside this
suite, and its source is <https://github.com/trl11/trl-pmu-firmware>, the
submodule at `extras/trl-pmu-firmware`. Committing the built image is what lets
a rig in a beam room program a part without a Microchip toolchain: 130KB and
`ipecmd` is the whole requirement, where building needs several gigabytes of
XC8 and the device pack.

Its version is read from its own bytes: the firmware embeds the same `fw=`
string it answers the console with, so the image and the board are compared on
the same string. Updating the image is overwriting that file, keeping the name,
and reflashing the parts on the bench.

    tools/bench/pic_flash.py                # flash the committed image, then read it back
    tools/bench/pic_flash.py --build        # build from the submodule first
    tools/bench/pic_flash.py --verify-only  # just ask the board what it is running

That tool exists rather than `make -C extras/trl-pmu-firmware flash` because
of two things a bare flash does not do. It retries through a programmer reset,
since the PKoB4 answers USB and then fails its bulk transfer often enough that
one attempt is not an answer — and **a failed flash leaves the part erased**,
because ipecmd erases before it programs, so stopping at the first error
leaves a blank part rather than the one you started with. And it finishes by
opening the console and reading the version back, so a part that flashed but
cannot talk is found now rather than at the first dose step.

Every run then reads that version off the board and records it beside the
image's SHA-256, in `manifest.json`:

```json
"dut": {"firmware": "<version>", "api": "<register map>", "build": "production"},
"firmware_image": {"name": "pmu3_firmware.hex", "sha256": "<digest>"}
```

A part running something other than the committed image is warned about and
recorded as a mismatch rather than measured quietly, because a characterisation
whose image is not the one the tree claims cannot be compared with anything.

## The measurement

TID on a microcontroller does not arrive as one failure. It arrives as
parameters walking, and then as the part stopping. So the run records seven
things every tick and fails only on the last of them.

**The telemetry stream.** *(implemented)* `app/frame.c` emits a
machine-readable frame on the debug UART: `@B` once at boot with the reset
cause and the firmware build, `@T` about every 1.16 s as a liveness beat, `@E`
per detection, and `@S` every 60 s with every counter. Section 4.11 of the
firmware reference says why it exists, and the reason is the one that matters
here — every detector in that firmware runs *inside* the part being measured,
so a hung superloop stops them silently and the silence reads as a clean run.
The beat is what turns silence into a detected condition.

Three numbers come off it. Detections, per code, which are the campaign's
data. Reboots, from the `boot` field, which are the hard end point. And the
board's own millisecond count against the host's clock, which is Timer0 and
therefore the oscillator — at far coarser resolution than a logic analyzer on
the UART would give, but for nothing, from frames that had to be read anyway.

`seq` and `ms` are redundant on purpose and the redundancy is itself a
detector: `seq` is a RAM counter and so an upset target, `ms` comes from
Timer0. `frames.classify` implements section 7.4's table over the two, which
separates a corrupted counter from a lost frame from a stopped clock. Neither
field is enough alone.

The stream is off at boot and the suite turns it on — by reading the state
with console `n` first, because `@` toggles, and a board that had the stream
restored from its stored configuration would be turned *off* by a blind
keystroke.

**Supply current.** *(needs the supply)* Leakage is the first TID signature in CMOS, and it shows
in `IDD` long before anything misbehaves. The only instrument on this bench
that measures it is the supply's own readback, which is milliamps against a
part drawing single-digit milliamps.

That is a real limitation and it should be stated rather than worked around:
**the early leakage curve is not recoverable at this resolution.** What a
milliamp step catches is the late, coarse rise — a part going from 5 mA to
15 mA — which is a genuine end-of-life signature but arrives long after the
first microamps would have. If the leakage curve is what the campaign wants,
it needs a current measurement this bench does not have, and that is a
decision to take before the beam rather than a number to apologise for after.

Sample it every tick anyway, as `idd_ma`, and chart it. It is free, and the
coarse rise is worth having.

**Brown-out trip point.** *(needs the supply)* The PMU's configuration bits enable BOR at about
2.45 V, and a BOR reference is a bandgap, so its trip point moves with dose.
Sweeping the supply down until the part stops answering, and back up until it
does again, finds it. `RESET_CAUSE` (`0x40`) and `RESET_COUNTS` (`0x43`–`0x4A`)
say whether it was BOR that took it, which is what tells a shifted trip point
from a part that simply hung. The two numbers are `bor_trip_v` and
`recover_v`, and the gap between them is the reset hysteresis.

Watch the sweep on the analyzer rather than through the bus. Read through I2C,
the moment of death is a transaction that timed out, which is worth tens of
milliseconds and cannot say whether the part stopped or the bridge did. On the
analyzer the UART simply stops mid-character and the enable pins fall, both
timestamped to the microsecond, and the recovery is the ramp coming back. That
turns a trip point inferred from a failure into one that was observed.

The supply's voltage readback is ten millivolts, which is fine for a trip
point near 2.45 V, and it reads at the supply's terminals rather than at the
pin. At single-digit milliamps the drop down two metres of wire is a few
millivolts, so the distinction does not matter here — it would if this part
drew a hundred times more.

Because BOR fires first, this sweep does not find the core's true `Vmin`. If
that number is wanted, it needs a second image with BOR disabled, and the
campaign should decide which it wants before the beam rather than after.

**Oscillator drift.** *(needs the analyzer)* The PIC runs off HFINTOSC, and the firmware's own
millisecond time base is confirmed to −0.8%. There is no need to add a test
pin for this: the debug UART's bit period is derived from the same oscillator,
and pressing `a` on the console leaves it emitting a status line once a second
at 115200 8N1 — 8.68 µs a bit, nominal. The logic analyzer clipped to that
line, capturing at 1 MHz, measures the bit period against its own 48 MHz
crystal. Ten thousand bit times resolve far finer than the drift being looked
for, and the same capture is a liveness check: a part that has stopped stops
talking.

**Sequencer timing.** *(needs the analyzer)* The power-on sequencer in `app/power_seq.c` ramps the
rail enables in a fixed order at fixed intervals, and those intervals come off
the same millisecond time base. Clipping the four enable pins and capturing
across a reset gives every stage boundary directly — which is a second
oscillator measurement over seconds rather than microseconds, and the two
disagreeing is more interesting than either drifting.

It is also the only functional check here that exercises the part's actual job.
A stage arriving out of order, or not arriving, is the PMU failing to sequence
rather than a parameter walking, and the BOR sweep produces one of these ramps
every time it recovers — so the measurement costs nothing beyond the clips.

**ADC and reference drift.** *(needs the bridge)* `JETSON_IS_RAW` (`0x06`–`0x07`) is unscaled ADC
counts by design — the interface publishes raw counts precisely so that no
unconfirmed scaling constant sits between the converter and the reading, which
makes it the right register to watch a converter through.

Feed its input (`RA1`) from a two-resistor divider off the DUT's own VDD,
proportioned for about mid-scale. The PIC's converter is referenced to its
supply, so a divider off that supply is ratiometric: the reading is a fixed
fraction of full scale whatever VDD is doing, the supply cancels, and
everything left that moves is the converter — offset, gain and the reference
behind it. That is the whole measurement, and it needs no second instrument
watching the divider, which is the reason to prefer it over a fixed voltage
from outside.

Use thin-film or metal-film resistors. A carbon composition divider in the
beam has its own dose response and would be indistinguishable from the drift
being measured.

**Digital and non-volatile integrity.** *(needs the bridge)* Four cheap checks,
none needing a firmware change:

- `FW_VERSION` (`0x08`) must read `0x06` every tick. It is one byte served
  from an interrupt handler over a stretched clock, and it failing is the
  transport failing.
- `WRITE_DROPS` (`0x09`) must not move. It counts bytes the deferred-write
  queue discarded, and filling that queue takes more than fifteen writes in a
  loop pass, which this suite does not do.
- The reset counters must not move except where the sweep moved them. A
  `WDT`, `STKOVF` or `STKUNF` count climbing is the hard end point.
- I2C1 decoded off the wire agrees with what the host thinks happened. The
  host sees a transfer fail; SDA and SCL together say *why* — a NACK, a
  corrupted byte, or a clock stretched past the bridge's own timeout. Under
  dose the transport degrades before it stops, and this is what separates a
  slowing part from a cable. `tools/la.py decode i2c1` in the firmware repo
  already does the decode with the pin names on it.
- EEPROM still takes a write: `HTR2_OFF_C` (`0x14`) alternated between two
  in-band values once a tick, read back after a settle. Charge pumps degrade
  before flash reads do. Write a *threshold*, never `RAIL_REQUEST` — see the
  warning in §4.7 of the interface document about the EEPROM behind it.

The flash checksum would be the sixth, and it is not usable. `hal/mcu_crc.h`
says so itself: the board's number and the number computed from the linked
image disagree on a Curiosity HPC, and every way of getting a CRC wrong
returns a plausible number rather than an error. Record it if it is read at
all, and do not let it decide anything until `tools/flash_crc.py` and the
board agree.

## What fails a tick

Soft parameters are recorded, not judged: current, trip point, bit period and
ADC counts are the curve the campaign exists to draw, and a run that failed
whenever one of them moved would fail on the first dose step.

A tick fails when the part stops being the part. Today that is the stream
going quiet past `pass_criteria.silence_timeout_s`, or the boot count moving,
which is the part having restarted under us. As the bench grows it is also
`0x28` not answering, `FW_VERSION` not reading `0x06`, a reset counter other
than BOR moving, or a recovery ramp that did not sequence.

The silence timeout wants a few seconds and not one. The beat is
loop-quantised rather than 1 Hz — measured at a steady 1158 ms on a Curiosity
HPC against a 385–485 ms loop — so a one-second timeout would fail a healthy
board on its first tick. Those are the hard end point, and the dose at which
the first of them lands is the number the campaign wants.

## Temperature is not measured, and it matters

Both the damage rate and the annealing that follows it are temperature
dependent, and nothing on this bench reads the package. The part's own
`TEMP0` and `TEMP1` are not a substitute: they are read through the converter
this test is characterising, so a drifting temperature and a drifting ADC are
the same number.

So record the beam room's ambient from the facility alongside each dose step,
by hand if that is what it takes, and keep it with the run. A characterisation
without the temperature it was taken at is hard to compare against anyone
else's.

## The bench

Three capabilities — `i2c`, `logic`, `psu` — none of which the suite requires
yet, because none of them is wired.

The debug UART is not among them and will not be. There is no `serial`
capability in Gauntlet, and `suite/console.py` opens the tty itself through
`termios`, which is the standard library and installs nothing. That matters
here: this suite runs in a beam room off a deployed bundle, where a missing
dependency is a beam slot rather than an error message. If a second suite ever
wants a serial port, that is the point at which a capability is worth adding,
and not before.

```
                                            ~2 m, beam room
    +----------------+                  |
    |      Host      |  USB  +--------+ |  I2C1, 100 kHz    +--------------+
    |   /dev/i2c-N   |------>| CP2112 |-+------------------>|              |
    +----------------+       +--------+ |  RC3 SCL/RC4 SDA  |              |
            |                           |                   |  PIC18F26K83 |
            |    USB    +--------+      |  3V3, GND         |     DUT      |
            +---------->| HM310T |------+------------------>|              |
            |           +--------+      |                   +--------------+
            |                           |                     |          |
            |    USB    +--------+      |   8 probes          |          |
            +---------->| FX2LP  |<-----+---------------------+          |
                        +--------+      |                                |
                                        |  on the carrier:               |
                                        |  VDD divider into RA1 ---------+
```

All eight analyzer channels earn a pin:

| Probe | Pin | Signal | What it is for |
|---|---|---|---|
| 1 | `RC5` | UART TX | Bit period, so oscillator drift. Liveness |
| 2 | `RC3` | I2C1 SCL | Clock-stretch width, so core speed |
| 3 | `RC4` | I2C1 SDA | With SCL, the decoded bus and its errors |
| 4 | `RA2` | `JETSON_EN` | Sequencer stage, and the first thing to fall |
| 5 | `RA3` | `EN_5V1` | Sequencer stage |
| 6 | `RA4` | `EN_5V2` | Sequencer stage |
| 7 | `RA5` | `EN_5V_ACC1` | Sequencer stage, RUN mode only |
| 8 | `RA7` | `EN_5V_ACC2` | Sequencer stage, RUN mode only |

`tools/la.py maps` in the firmware repo is where the project keeps its probe
maps, and captures are named for the map they were taken with because nothing
inside a `.sr` file records which pins were clipped. If this map is not in
there, add it there rather than keeping it only here — a map given wrongly
relabels every channel with a confident firmware name and says nothing.

**The two captures run at different rates and cannot be one capture.** A
115200 baud bit is 8.68 µs and wants 1 MHz to measure; a boot ramp spans
seconds and at 1 MHz would be millions of samples of nothing. So the bit
period is a short window at 1 MHz and the ramp is a long one at 100 kHz or
below, which is comfortably inside what the board delivers whole — the FIFO
limit that cuts a 24 MHz capture to 0.68 ms is nowhere near either of these.

Probe 2 is the one that is easy to leave off and worth having. The PMU serves
every I2C1 byte from an interrupt handler and stretches SCL until it has the
byte ready, so the width of that stretch is a direct measurement of how long
the core is taking to do a small piece of work. It is the one digital-timing
metric that needs no test pin and no firmware change, and it degrades before
anything stops answering.

What the analyzer cannot do is the measurement that was lost with the DAQ. It
is a digital instrument: it reports levels and edges, and there is no
arrangement of it that reads a few microamps of leakage. The current
resolution stated above is the resolution, and the analyzer buys timing rather
than current.

Three things about this bench are not settled and should be settled on a
rehearsal, not in the beam room:

**The DUT has to be the only thing in the beam.** A Curiosity HPC works today
and the firmware runs on it unmodified — but the whole board would be dosed,
and its regulator or its on-board programmer failing looks exactly like the
DUT failing. The campaign's test vehicle is still `TBD`; what it should be is
a carrier holding the SSOP-28 part, its decoupling, the ADC divider and one
connector, with everything else outside. A 28-pin DIP extender out of the HPC
socket is the cheap version of the same idea, and the HPC's socketed part is
`-E/SP` where the flight part is `-E/SS`, so an HPC result is a rehearsal and
not the datum.

On an HPC, three of the sequencer pins in the probe map above — `RA4`, `RA5`
and `RA7` — already have LEDs on them, which is a free visual confirmation of
the ramp and does not stop the analyzer reading the level. The two to avoid
are `RA6`, whose LED and series resistor load an analog input directly, and
`RA0`, where the potentiometer wiper sits on a push-pull output. `RA1` is
unloaded and brought out on the expansion header, so the divider can go
straight on it. Read
[`docs/guides/hpc-devboard.md`](../../../../extras/trl-pmu-firmware/docs/guides/hpc-devboard.md)
before clipping anything onto a dev board rather than a carrier.

**I2C is not a cable bus.** Two metres at 100 kHz with 1.5 kΩ pull-ups is
about the limit; past that it wants a buffer. Drop the clock rather than raise
the pull-ups, since the part imposes no minimum.

**Clock stretching has to actually work through the CP2112.** A host with
stretching disabled reads corrupted data rather than failing, which is a
failure mode that would be read as dose.
`tools/i2c1_conformance.py` in the firmware repo is the check for exactly this
and everything else in the interface document, and it should pass on the bench
before the first exposure. It writes to the board, so do not run it on
something that minds a rail moving.

## What to run

In order, and the first four need no beam:

1. `tools/i2c1_conformance.py` from the firmware submodule, against the DUT on
   the CP2112. The transport is either right or this test measures nothing.
2. `pmu3 status`, for a reading a human can look at, and `pmu3 doctor` when
   nothing is found. `pmu3ctl/src/pmu3ctl/device.py` is the reference driver
   for everything the suite will do, and is standard library only.
3. `tools/la.py maps`, then `capture` and `timeline` across one deliberate
   reset, to confirm every probe is on the pin its name claims and that the
   ramp reads as stages rather than as eight anonymous channels.
4. `bench.yaml` — half a minute, the whole measurement, the bench as it will
   be. Run it after every change to the wiring. `smoke.yaml` is the same code
   against a synthesised stream, so it says the suite works and nothing about
   the bench.
5. `standard.yaml` at each dose step, and `continuous.yaml` through an
   exposure, which has no end of its own and is stopped by the operator at the
   target dose.

Then anneal. TID is not finished at the end of the beam: room-temperature
annealing recovers part of the shift and the accelerated anneal of MIL-STD-883
TM1019 (100 °C, 168 h, biased) is what separates recovered parameters from
permanent damage. That is `bench.yaml` at intervals afterwards, against the
same DUT on the same bench, and it wants planning now because it needs the
bench kept standing for a week after everyone thinks the test is over.

Keep the part biased and running its normal superloop throughout the exposure.
Unpowered is not the worst case for CMOS TID, and a part that was dark for
half the dose has no comparable number.

## The firmware change this would be better with

None of the above needs one, which is the point. But two numbers are behind
the UART rather than the bus — the loop period `app/loop_stats.c` already
keeps, and the flash checksum — and a UART is a second cable into the beam
room and a second thing to go wrong. Exposing both as a small read-only block
in the host register map would put the whole measurement on one bus. It is a
`FW_VERSION` bump to `0x07` and an additive one, so a host that does not know
the block is unaffected. Worth doing if the schedule has room; not worth
blocking on.
