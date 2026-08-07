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
| `chamber` | nothing | simulation only |

Beside each is a simulation — `mock_psu.py`, `mock_daq.py`, `mock_chamber.py` —
which exists for development and for tests, and which reaches an operator only
when they ask for it.

## What is registered

`instruments/detect.py` decides, at startup and again on every operator scan.
An instrument is registered only while its hardware answers, so the page tracks
the bench: plug something in and a scan picks it up, unplug it and a scan drops
it.

| Setting | Meaning |
|---|---|
| `psu_port`, `daq_serial` | `"auto"` probes, `""` does not look at all, anything else is the serial port or USB serial number to use |
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
are stated once.

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
  dragging, by clicking a point on it, or by the arrow keys. Its numeric entry
  stays beside it, since a dial cannot be typed into.
- Commands are keys. One with no fields is a plain key; `danger` tints it red.
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

A rejected command is a 422 carrying the provider's own words; an instrument
that takes no commands at all is a 405.

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
`system/99-gauntlet-instruments.rules` settles, and `make install-udev-rules`
installs it:

```
$ make install-udev-rules
==> installing 99-gauntlet-instruments.rules into /etc/udev/rules.d
==> instruments the rules cover
  0683:2008  serial 6A046A27 DI-2008  /dev/bus/usb/003/061  root:dialout 660  OK
```

Run it on whichever host the instruments are plugged into. It refuses to run
anywhere without udev rather than appearing to succeed, because the devcontainer,
the server image and the desktop app all see what the host's rules decided rather
than setting it themselves — installing the file inside a container changes
nothing.

`make udev-check` is the report on its own, and does run in the devcontainer:
it asks what `/dev` looks like now, not what udev was told. Both read the vendor
ids out of the rules file rather than repeating them, so a rule added there is
covered without touching `scripts/udev_check.py`.

Until the rule is installed the node can be opened by hand with
`sudo chgrp dialout /dev/bus/usb/<bus>/<device> && sudo chmod 660` on the same
node `udev-check` names, which lasts until the device is replugged.
