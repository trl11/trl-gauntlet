# Instruments

An instrument is a capability provider seen from the operator's side: the bench
supply, the acquisition unit, the chamber. Gauntlet owns the device, a suite
drives it through a granted URL, and the operator drives the same object from
the Instruments page. See [`architecture.md`](architecture.md) for why one
object has two words, and [`frontend.md`](frontend.md) for the rest of the UI.

Nothing outside `gauntlet.instruments` knows one instrument from another. The
API, the panel and this document describe machinery that reads what a provider
declares about itself; naming an instrument anywhere else is a defect.

## What ships

| Name | Backed by | Attached over |
|---|---|---|
| `psu` | Hanmatek HM310T, `instruments/hm310t_psu.py` | Modbus RTU on a USB serial port, 9600 8N1, slave 1 |
| `daq` | DATAQ DI-2008, `instruments/di2008_daq.py` | vendor bulk-USB protocol, claimed through usbfs |
| `camera` | any UVC camera, `instruments/uvc_camera.py` | V4L2 ioctls on a `/dev/video*` node, memory-mapped capture |
| `i2c` | Silicon Labs CP2112, `instruments/cp2112_i2c.py` | `I2C_RDWR` on the `i2c-dev` node the kernel's own `hid-cp2112` driver adapts it to |
| `logic` | any Cypress FX2LP eight-channel analyzer, `instruments/fx2_logic.py` | vendor bulk-USB, claimed through usbfs, once sigrok's fx2lafw is loaded into it |
| `chamber` | nothing | simulation only |

Beside each is a simulation — `mock_psu.py`, `mock_daq.py`, `mock_camera.py`,
`mock_i2c.py`, `mock_logic.py`, `mock_chamber.py` — which exists for
development and for tests, and which reaches an operator only when they ask
for it.

Its eight channels are settled by one `configure` command carrying a row each,
rather than a control that picks a channel and a control that sets it. A row
takes a mode, a label, or both, and a channel no row names is left alone — so
the panel sends all eight at once and a suite sends the one it cares about,
through the same command. Every row is checked before any of it is applied,
and the scan list is reloaded once for the lot rather than once per channel.

A channel is named rather than numbered wherever it is named: its label goes
into what `readouts()` declares, so the panel, the dashboard tile and the chart
legend all read "Rail 3V3" in place of "CH 1" without any of them knowing a
label from a channel number. Labels live in the driver for the session, as
channel modes do.

The DI-2008 scans at `clock / (srate * dec)` across its whole scan list, and
the clock is not the fixed 8 kHz the base clock suggests: a list of one channel
runs at 8000 Hz, and any longer list at 800 Hz. The driver reads `info 9` back
after loading the list rather than assuming, because that tenfold difference is
what decides whether a capture window long enough to hold a scan is 0.1 s or a
second. It also sizes its capture from that rate, so a sample costs what the
configured rate needs and no longer.

The camera is driven through V4L2 ioctls against structures laid out to match
`videodev2.h`, and its frames are converted and written by `instruments/
imaging.py`, so nothing is installed to read a camera. A GMSL sensor behind a
GMSL-to-USB adapter arrives as an ordinary capture device, and the driver never
learns it was anything else.

Its node is held open and left streaming for as long as something *owns* it,
not merely for as long as the instrument is registered — see
[Owning a device](#owning-a-device) below. A capture device is exclusive, so
holding it is what stops another process taking the camera part-way through a
run, and starting a 4K stream costs far more than keeping one running between
snapshots. A `snapshot` discards whatever the driver had already queued and
reports the frame after it, because a queue that has been sitting still holds
the picture from whenever it was last looked at.

YUYV is converted and scaled in one pass and written as a PNG; MJPEG is already
a JPEG and is written out byte for byte. Every snapshot is measured for mean
brightness and an edge score, which is what lets a suite tell a picture from a
lens cap without decoding anything itself. A camera offering neither format is
reported as unavailable rather than registered.

Two different permission failures are told apart, because the fix differs:
`EACCES` is the account not being in the `video` group, and `EPERM` on a node
that is plainly there is a container's device cgroup lacking a rule for char
major 81.

### What the link reports, behind a GMSL adapter

A GMSL camera reaches the host through a serializer in the head and a
deserializer in the adapter, and Leopard Imaging's adapters tunnel I2C over a
vendor extension unit on the same connection that carries video. So the chips
can be read with nothing wired to the board. `instruments/gmsl.py` does that,
and the camera picks it up on connection: a webcam finds no chips and simply
carries on, so the telemetry is an extra rather than a requirement.

The `link_status` command reports every chip's device id, revision, lock state
and error counters, and it appears on the panel only when chips answered.
**The chips' counters clear when they are read**, so a reading is the errors
since the previous one and whoever reads them consumes them. `UvcCamera` is
the only reader and keeps the running totals, which is what stops a panel
refresh and a suite's sample stealing counts from each other.

Nothing reads a register with the write bit set. A write can take the link
down, and a link that drops part-way through an irradiation looks exactly like
a radiation effect, which costs a run its meaning rather than just its data.

`stream_stats` measures what the link is carrying: it reads frames back to
back for a short burst and reports frame rate, data rate, frames dropped and
frames the driver flagged as corrupt. It is separate from `snapshot` because
the two cannot be measured together. The driver fills a buffer only while one
is free, so a caller taking a frame every second measures its own sampling
rate; the queue sits full in between and the frames arriving then are never
counted. The burst drains that backlog before it starts timing, or the
sequence gap left by the caller's own pause would be reported as dropped
frames.

The CP2112 is a HID device, but the kernel's `hid-cp2112` driver already
speaks its report protocol and adapts it to an ordinary `i2c-dev` node — one
named `"CP2112 SMBus Bridge on hidrawN"`, which is how `candidate_adapters()`
tells it apart from whatever other I2C adapters the host exposes. The driver
never touches a HID report itself; it opens that node and issues `I2C_RDWR`,
which is also what lets `write_read` hold the bus for a register's address
and its reply with no stop between them, the way a device that needs a
repeated start requires. There is no fixed device on the other end of the
bus: a suite names the address and the bytes itself, through `write`, `read`
and `write_read`, the way it would with any I2C bridge.

### The analyzer that arrives without firmware

The cheap eight-channel analyzers — Xicoolee's among them — are all the same
board: a Cypress FX2LP (CY7C68013A) with its port B wired to the probes, no
acquisition logic of its own and nothing in it worth calling firmware. What
makes one a logic analyzer is fx2lafw, sigrok's firmware for the part, written
into its RAM over USB. Until that is in it the board answers only its
bootloader, so `instruments/fx2_logic.py` loads it and then speaks its
protocol, both taken from libsigrok's `src/hardware/fx2lafw` and `src/ezusb.c`.

**The firmware is sigrok's and is not shipped here.** `logic_firmware` says
where it is: `"auto"` searches the directories `sigrok-firmware-fx2lafw`
installs into, and a path names a file or a directory to load it from instead.
A board with no image to load is registered anyway and reports which file it
wanted, because "install this package" is a fault to show rather than
something to hide.

What tells a loaded board from an unloaded one is not its USB ids. fx2lafw
keeps whichever ids the EEPROM carries — `0925:3881` for the Saleae clones,
`04b4:8613` for a bare part — and changes the descriptor strings, which read
`sigrok` and `fx2lafw` once it is running. Loading renumerates the board: it
drops off the bus and comes back a second or two later. So a load is one
probe and the capture is the probe after it, which is why nothing waits and
`available()` says what it is waiting for.

A capture is one window of samples at one rate: a byte per sample, a bit per
channel, straight off bulk endpoint 2. There is no trigger and no stop
command — the firmware fills its FIFO and stalls there — so a capture drains
the endpoint before it starts, or it would read the tail of the one before it.
The rates on offer are the ones that divide the board's 48 or 30 MHz clock
exactly, since a rate is a divisor rather than a setting, and a capture is
measured against the rate it was asked for rather than one rounded to.

**The window is a request.** libsigrok keeps thirty-two reads in flight so the
endpoint is never unattended; one synchronous reader has one, and the gap
between two of them is enough for the board to overrun and stop sending. On
the bench board a whole window arrives at 1 MHz and below, and 24 MHz gives
one 16 KiB FIFO — 0.68 ms of signal — before it goes quiet. So a capture
reports the window it got rather than the one it asked for, and a short one is
what the board gave rather than a fault. For a look at a fast edge, which is
what the fastest rates are for, that is the window wanted anyway.

What comes back is what each probe did over that window — its level, its
edges, its duty and the frequency those imply — and a picture of the capture
itself, drawn by `instruments/waveform.py` and returned the way a camera
returns a snapshot. Eight channels of a few million samples are measured
without a loop over them: one channel is a 256-entry translation of the
stream, and its edges are one exclusive-or of that against itself shifted by a
sample. The samples themselves come back too, in `samples_base64`, so a suite
can record what was captured rather than only a drawing of it. A suite writes
either into its run directory and names it in `metrics.traces`, which is what
gives the run its own Traces tab — a picture is shown as one, and sample data
is drawn as lanes the operator can scroll and zoom. See
[`contract.md`](contract.md) for both shapes.

## What is registered

`instruments/detect.py` decides, at startup and again on every operator scan.
An instrument is registered only while its hardware answers, so the page tracks
the bench: plug something in and a scan picks it up, unplug it and a scan drops
it.

| Setting | Meaning |
|---|---|
| `psu_port`, `daq_serial`, `i2c_serial`, `logic_serial` | `"auto"` probes, `""` does not look at all, anything else is the serial port or USB serial number to use. Most analyzer boards carry no serial number, so `"auto"` takes the first on the bus |
| `camera_device` | `"auto"` registers so long as any `/dev/video*` node exists, `""` does not look at all, anything else is the node to register. Which node actually streams, and whether it carries a format the encoder can write, is not settled until something owns it — see [Owning a device](#owning-a-device) |
| `camera_format` | `"auto"` reads a frame to decide what it really carries, or name `yuyv` or `raw10_rggb` to state it. A GMSL adapter reports YUYV over UVC while sending raw sensor data, and the UVC format code cannot tell them apart |
| `logic_firmware` | Where fx2lafw is. `"auto"` searches the directories `sigrok-firmware-fx2lafw` installs into; a file or a directory names it instead |
| `simulated_instruments` | Names the instruments to simulate instead of probing for. Empty by default |

An explicitly named device stays registered even when it goes quiet, reporting
why through `unavailable_reason`: the operator said there is one there, so its
absence is a fault to show rather than something to hide. An automatic PSU
probe opens only known USB-serial bridges, or it would write Modbus frames at
whatever else is sitting on a serial node.

A scan never rebuilds a provider whose hardware still answers, because that
would drop the connection the panel is reading through. Real and simulated are
told apart by `describe()["driver"] == "mock"`, never by class name.

`available()` is called on every UI poll, so it answers from cached connection
state rather than touching the device, and re-probes at most every few seconds.
A failed register read leaves that one value `None` rather than raising, so one
flaky exchange cannot abort a long run.

## Owning a device

Some devices are exclusive to open at all, not just to drive: a UVC node
admits one owner, and opening it is enough to matter even before anything is
captured. For those, `available()` and detection answer from presence alone —
whether a node exists — never by opening it, so a scan and a panel poll do not
themselves claim the camera the way they always claimed the PSU's port. The
camera is the only instrument like this today; `instruments/uvc_camera.py`
implements `OwnableCapability` (`owned()`, `own()`, `disown()`) from
`capabilities/registry.py`, and a provider that does not need this stays a
plain `CapabilityProvider`.

Something has to own it before it opens:

- **The operator**, through the panel's `set_owned` latching key — the same
  mechanism as a PSU's output, just settling "is the device open" instead of
  "is the rail live".
- **A run**, for exactly its duration. `CapabilityRegistry.claim_for_run` is
  called before a suite is spawned: it owns whichever of the suite's
  `requires:` are `OwnableCapability` and not already owned, and hands back a
  release the supervisor calls once the run ends, however it ends. A
  capability the operator already had open is left exactly as found — a run
  never disowns what it did not own — so the bench reads the same after the
  run as it did before it, whether that was closed or open.

A suite itself never calls `own()`: it is granted the capability the same way
as any other and drives it through `POST /api/capabilities/camera`, unaware
that the device only opened because the run claimed it.

## What a provider declares

Four members are the whole obligation: `name`, `available()`, `describe()` and
`instance_id()`. Everything else is an optional facet, each a runtime-checkable
protocol in `capabilities/registry.py`. A provider that omits one degrades to
empty state, no commands, or a 405 — it never fails.

| Facet | Members | Gives it |
|---|---|---|
| `ReadableCapability` | `read()` | `GET /api/capabilities/{name}`, and state when there is no `state()` |
| `WritableCapability` | `write(values)` | `POST /api/capabilities/{name}` |
| `StatefulCapability` | `state()` | The values the panel draws |
| `CommandableCapability` | `command(name, args)`, `commands()` | One control per declared command |
| `PresentableCapability` | `connection()`, `primary_command()`, `readouts()` | Says how the panel lays its state out |
| `OwnableCapability` | `owned()`, `own()`, `disown()` | Opening the device is deferred to an explicit claim — see [Owning a device](#owning-a-device) |

`capabilities/declare.py` builds the dictionaries the last two return, so every
provider describes itself in the same shape:

```python
def commands(self) -> list[dict[str, Any]]:
    return [
        {
            "name": "set_voltage",
            "label": "Set Voltage",
            "fields": [command_field("voltage", "Voltage", unit="V", minimum=0.0, maximum=30.0)],
        },
        {
            "danger": True,
            "name": "set_output",
            "label": "Set Output",
            "fields": [command_field("enabled", "Enabled", "boolean")],
        },
    ]

def readouts(self) -> list[dict[str, Any]]:
    return [
        readout("voltage", "Voltage", precision=2, unit="V"),
        readout("current", "Current", precision=3, unit="A"),
        readout("voltage_setpoint", "Set V", precision=1, role="summary", unit="V"),
        readout("output_enabled", "Output", role="summary"),
    ]
```

`readout` names a dotted path into `state()`. `role` is `"headline"` for a
reading the display burns large or `"summary"` for the row beneath, and `group`
splits a multi-channel instrument into sections. `command_field` describes one
argument: its type, unit, choices, and its minimum and maximum. `number_arg`
reads that argument back and rejects what the field ruled out, so the bounds
are stated once. A ranged field gets a dial by default; `dial=False` keeps it
a plain entry, for a value an operator types exactly rather than sweeps — an
address, a count. `choices_from` names a `state()` key holding values found at
runtime — what a "detect" command discovered — offered beside the entry as
quick picks; typing one by hand still works. `format="hex"` draws those picks
(and nothing else — an operator still types decimal unless the field's own
entry is hex too) as bare hex with no `0x`, for a value read the way it is
written on a datasheet — an I2C address, say.

Commands that never run at once and always act on the same thing — a write
and a read of the same address — share a `group` key so the panel draws their
fields once and a key per command beneath, rather than one bordered card per
command each repeating the field the last one just took. A field two commands
in the group both declare is drawn once; a field only one of them takes is
still only sent by that one. Put the field a command needs first in its own
`fields`, even one it shares, so the group's controls read in a sensible
order regardless of which command happens to declare a shared field first.

A command that settles the same fields for several things at once — the
channels of an acquisition unit, the rails of a supply — adds `command_row` per
thing and a `row_label` for the column naming them:

```python
{
    "name": "configure",
    "label": "Apply",
    "row_label": "Channel",
    "rows": [command_row(name, f"CH {name}", {"label": ..., "mode": ...}) for name in channels],
    "fields": [
        command_field("mode", "Mode", "string", choices=MODES),
        command_field("label", "Label", "string"),
    ],
}
```

The panel draws that as a table with the fields for columns, each control
starting at the value its row carries, and sends every row back under `rows`
keyed by `command_row`'s `key`. The provider decides what a row is worth
offering: a value it would be wrong to apply back — a label that is really a
fallback — belongs out of `values`, or the operator applies it as if they had
typed it. A row-wise command never becomes a latching key, since the key
stands for one boolean and a table has as many as it has rows.

## How the panel draws it

`InstrumentPanel` renders every instrument, from the declarations above and
nothing else. A provider that declares no readouts still gets a panel: every
state value, as a flat table of dotted keys.

- Readings light a seven-bar display. Headline readouts cycle green, red and
  amber in the order they were declared, which lands a supply on green volts,
  red amps and amber watts without anything knowing it is a supply. Summary
  readouts burn amber beneath them. A value that is boolean true burns green
  whatever its position, the way an indicator lamp does, and a reading seven
  bars cannot spell falls back to plain text.
- A field declaring both a minimum and a maximum gets a dial, turned by
  dragging, by clicking a point on it, or by the arrow keys, unless it
  declares `dial=False`. Its numeric entry stays beside it, since a dial
  cannot be typed into. A field declaring `choices_from` also gets a row of
  quick-pick buttons beside its entry, drawn from whatever list that
  `state()` key currently holds — below a group's shared toolbar rather than
  under the entry itself, since it is what pressing one of those keys found,
  not another control beside them.
- Commands are keys. One with no fields is a plain key; `danger` tints it red.
- The header carries a collapse toggle for every instrument, whatever it
  declares; an operator not using one gets it out of the way, and the panel
  remembers the choice per instrument across a reload.
- The `primary_command` gets the panel's width. If it settles a single boolean,
  and otherwise only picks what to settle it for, it becomes a **latching key**:
  pressing it sends the opposite of what it last sent. A lock beside it has to
  be released first, and stays where the operator leaves it.

## While a run holds it

Both API halves reach the same registry, so a panel shows live state while a
suite is mid-run: neither side holds the port, so both can ask.

`GET /api/instruments` reports `in_use_by`, the id of the in-flight run whose
suite declares that capability in `requires:`. The manifest is what says which
instruments a run holds, so the API learns this without knowing one instrument
from another. While it is set the panel names the run, keeps that instrument's
latching key locked, and will not let the operator release the lock — taking
the output by hand mid-run would cut across the test. A run taking the
instrument also leaves the lock shut behind it, so nothing goes live again
untouched. The instrument's other commands stay drivable.

## Endpoints

| Endpoint | For |
|---|---|
| `GET /api/instruments` | Every instrument: state, commands, readouts, `available`, `in_use_by` |
| `GET /api/instruments/{name}` | One of them |
| `POST /api/instruments/rescan` | Run detection again and report what is registered afterwards |
| `POST /api/instruments/{name}/command` | Drive one, as `{"command": ..., "args": {...}}` |
| `GET|POST /api/capabilities/{name}` | The same providers, for the suite process |

A rejected command is a 422 carrying the provider's own words, on both halves:
a suite that asks for something the instrument does not offer reads why, rather
than a 500 that would have its run report a server fault. An instrument that
takes no commands at all is a 405.

There is no `GET /api/capabilities`. A suite is handed the capabilities it was
granted and discovers nothing else, and the bench an operator sees is
`GET /api/instruments`, which reports the same providers in more detail.

## Writing a provider

Put the device in `gauntlet.instruments` and satisfy the protocols from
`gauntlet.capabilities`; the second holds no device code and the first holds no
protocol. Register it in `detect.py` beside the others, under the capability
name a suite would ask for. Nothing in the frontend or in
`gauntlet.api.instruments` changes — declaring `commands()` and `readouts()` is
what builds the panel.

Two things a driver on a real bench owes the rest of the system:

- `close()` releases the port without changing what the device is doing.
  Detection calls it when it replaces a provider, and switching an output off
  there would cut power to whatever a run is driving.
- `available()` is polled constantly. Answer from cached state.

Tests construct providers against stand-ins rather than hardware:
`tests/test_instruments.py` covers the simulations, `test_instruments_real.py`
the two drivers against fake ports, and `test_instruments_api.py` the endpoints.

## Host access

The kernel's usbserial drivers already create `/dev/ttyUSB*` and `/dev/ttyACM*`
owned by `dialout`, which is why the PSU needs nothing installed. An instrument
driven over raw USB is claimed through usbfs, whose nodes default to
`root:root 0664` — enough to read descriptors, not enough to talk. That is what
`rig/99-gauntlet-instruments.rules` settles, and `rig/setup-host.sh`
installs it:

```
$ sudo ./setup-host.sh
==> installing udev rules into /etc/udev/rules.d
    99-gauntlet-instruments.rules
==> reloading udev
==> adding dev to dialout
    dev must log out and back in before this takes effect
==> instruments these rules cover
    0683:2008  /dev/bus/usb/003/061  root:dialout 660  6A046A27 DI-2008
```

`make install-udev-rules` runs that same script, so a checkout and a host that
only has an AppImage set themselves up the same way. It installs every `*.rules`
file beside it, so a rule added to the release needs no change to the script,
and it reads the vendor ids back out of those files to report on what it
covers. It refuses to run anywhere without udev rather than appearing to
succeed, because the devcontainer, the server image and the desktop app all see
what the host's rules decided rather than setting it themselves — installing
the file inside a container changes nothing.

The rules hand the nodes to `dialout`, which does nothing for a user who is not
in that group, so the script adds the invoking one and says that a session has
to be restarted before it counts.

`make app-build` copies the script, the rules and a `README.txt` into `dist/`
beside the installers. An installer cannot do any of this for the host it lands
on, so whoever unpacks a release has to, and the README is what tells them.

`make udev-check` is the report on its own, and does run in the devcontainer:
it asks what `/dev` looks like now, not what udev was told. Both read the vendor
ids out of the rules file rather than repeating them, so a rule added there is
covered without touching `tools/bench/udev_check.py`.

Until the rule is installed the node can be opened by hand with
`sudo chgrp dialout /dev/bus/usb/<bus>/<device> && sudo chmod 660` on the same
node `udev-check` names, which lasts until the device is replugged.
