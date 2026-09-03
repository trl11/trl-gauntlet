"""Continuously reads one I2C address through a CP2112 bridge and prints it.

Ad-hoc bench tool, not a suite: it drives ``gauntlet.instruments.cp2112_i2c``
directly so it shares the exact transfer logic the ``i2c`` capability uses,
rather than reimplementing the ioctl. Standalone in the sense that matters on
a bench — it does not require the caller to have activated the project
virtualenv first: if ``gauntlet`` is not already importable, it re-execs
itself under ``.venv/bin/python`` before doing anything else.

Written against the ADS7142-Q1 register map (``docs/datasheet/ads7142-q1.pdf``,
Section 7.6). Two things about it are easy to get wrong, and this tool used
to get the second one wrong:

* Its analog inputs default to CH0 only, in "manual mode" — a bare I2C read
  of 2 bytes returns one 12-bit conversion, MSB-first, no configuration
  needed. That is what ``--channel`` omitted gives you.
* There is no per-read channel-select register. Reading CH1, or CH0 and CH1
  alternately, requires switching into "manual mode with AUTO sequence"
  (``OPMODE_SEL`` = 100b), enabling the wanted channels in
  ``AUTO_SEQ_CHEN``, and setting ``SEQ_START`` in ``START_SEQUENCE`` —
  after which successive reads cycle through the enabled channels in
  ascending order on their own. Earlier versions of this tool instead wrote
  the channel number into ``CH_INPUT_CFG`` (0x24) before every read; that
  register actually selects the input *topology* (single-ended /
  pseudo-differential / remote-ground-sense), not a channel, so that write
  was silently reconfiguring the analog front end rather than picking CH1.
  ``configure_channels`` below resets ``CH_INPUT_CFG`` to its default
  (two-channel single-ended) every run to undo that if it happened before.

Read shapes:

* Plain read (default, ``--channel`` omitted or ``0``) — CH0-only manual
  mode, the power-on default.
* ``--channel 1`` — AUTO-sequence manual mode with only CH1 enabled.
* ``--channel all`` — AUTO-sequence manual mode with every channel from 0 to
  ``--channels - 1`` enabled (default ``--channels 2``, the ADS7142's own
  count; raise it for a part with more channels). Reads cycle through them
  in order.

``--watch`` redraws its rows in place (rather than scrolling) and highlights,
per channel, whether a reading moved from that channel's previous one —
green for a rise, red for a fall, plus a plain ``+N``/``-N`` delta for a
non-color terminal or a redirected log.

    python3 tools/mevo_temp_monitor.py --watch
    python3 tools/mevo_temp_monitor.py --channel all --watch --interval 0.2
"""

from __future__ import annotations

import itertools
import os
import sys
from pathlib import Path


def _ensure_venv() -> None:
    """Re-exec under the project's ``.venv`` if ``gauntlet`` is not already importable.

    Lets this run as ``python3 tools/mevo_temp_monitor.py`` from a plain shell
    that has not sourced the venv, the same way ``make dev`` puts you in one.
    """
    try:
        import gauntlet  # noqa: F401

        return
    except ModuleNotFoundError:
        pass
    venv_python = Path(__file__).resolve().parents[2] / ".venv" / "bin" / "python"
    if not venv_python.exists():
        print("gauntlet is not importable and no .venv found; run `make setup` first.", file=sys.stderr)
        sys.exit(1)
    os.execv(str(venv_python), [str(venv_python), __file__, *sys.argv[1:]])


_ensure_venv()

import argparse  # noqa: E402
import time  # noqa: E402

from gauntlet.capabilities.registry import CommandRejected  # noqa: E402
from gauntlet.instruments.cp2112_i2c import Cp2112I2c, candidate_adapters  # noqa: E402

# ADS7142-Q1 opcodes (Table 7-4) and Page1 register addresses (Table 7-5).
_OPCODE_SINGLE_REGISTER_WRITE = 0x08
_REG_OPMODE_SEL = 0x1C
_REG_START_SEQUENCE = 0x1E
_REG_ABORT_SEQUENCE = 0x1F
_REG_AUTO_SEQ_CHEN = 0x20
_REG_CH_INPUT_CFG = 0x24

_OPMODE_MANUAL_CH0_ONLY = 0b000
_OPMODE_MANUAL_AUTO_SEQ = 0b100
_INPUT_CFG_TWO_CHANNEL_SINGLE_ENDED = 0b00

_ANSI_RESET = "\033[0m"
_ANSI_GREEN = "\033[1;32m"
_ANSI_RED = "\033[1;31m"


def channel_arg(text: str) -> int | str:
    """``--channel`` takes an integer channel or the literal ``all``."""
    return "all" if text.lower() == "all" else int(text, 0)


def parse_args(argv: list[str]) -> argparse.Namespace:
    """CLI arguments for one monitoring run."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--address", type=lambda s: int(s, 0), default=0x18, help="7-bit I2C address (default: 0x18)")
    parser.add_argument(
        "--channel",
        type=channel_arg,
        default=None,
        help="Channel to read (0 or 1), or 'all' to cycle through every channel each read. "
        "Omit for CH0-only manual mode, the power-on default.",
    )
    parser.add_argument("--channels", type=int, default=2, help="Channel count for --channel all (default: 2)")
    parser.add_argument("--length", type=int, default=2, help="Bytes to read each cycle (default: 2)")
    parser.add_argument("--interval", type=float, default=0.5, help="Seconds between reads (default: 0.5)")
    parser.add_argument("--count", type=int, default=None, help="Stop after this many reads (default: run until Ctrl-C)")
    parser.add_argument("--node", type=Path, default=None, help="Adapter node, e.g. /dev/i2c-3 (default: auto-detect)")
    parser.add_argument(
        "--watch", action="store_true", help="Highlight a reading that moved from that channel's previous one"
    )
    return parser.parse_args(argv)


def _write_register(i2c: Cp2112I2c, address: int, register: int, value: int) -> None:
    """One opcode-prefixed single-register write."""
    data = f"{_OPCODE_SINGLE_REGISTER_WRITE:02x}{register:02x}{value:02x}"
    i2c.command("write", {"address": address, "data": data})


def configure_channels(i2c: Cp2112I2c, address: int, channels: list[int] | None) -> None:
    """Put the device into the mode that reads exactly ``channels``.

    ``channels=None`` means CH0-only manual mode. Otherwise this switches to
    manual mode with AUTO sequence and enables exactly the channels given;
    successive reads then cycle through them in ascending order on their
    own — there is no per-read channel-select register on this part.

    Always resets ``CH_INPUT_CFG`` to its two-channel single-ended default
    and aborts any sequence left running, so a previous run (or a stale
    device state) does not leave the analog front end in a differential or
    remote-ground-sense configuration a plain pot read was never meant for.
    """
    _write_register(i2c, address, _REG_ABORT_SEQUENCE, 0x01)
    _write_register(i2c, address, _REG_CH_INPUT_CFG, _INPUT_CFG_TWO_CHANNEL_SINGLE_ENDED)
    if channels is None:
        _write_register(i2c, address, _REG_OPMODE_SEL, _OPMODE_MANUAL_CH0_ONLY)
        return
    enable_mask = 0
    for channel in channels:
        enable_mask |= 1 << channel
    _write_register(i2c, address, _REG_AUTO_SEQ_CHEN, enable_mask)
    _write_register(i2c, address, _REG_OPMODE_SEL, _OPMODE_MANUAL_AUTO_SEQ)
    _write_register(i2c, address, _REG_START_SEQUENCE, 0x01)


def decode(raw: bytes) -> tuple[int, float]:
    """A code and its percent of full scale, from a 12-bit or byte-wide read."""
    code = int.from_bytes(raw, "big") >> (max(0, len(raw) * 8 - 12)) if len(raw) >= 2 else raw[0]
    full_scale = (1 << 12) - 1 if len(raw) >= 2 else (1 << (len(raw) * 8)) - 1
    pct = code / full_scale * 100 if full_scale else 0.0
    return code, pct


def format_delta(code: int, previous: int | None, use_color: bool) -> tuple[str, str]:
    """A colorized code string and a plain ``+N``/``-N``/`` `` delta, given the last code at this channel."""
    if previous is None:
        return f"{code:>5}", "    "
    delta = code - previous
    if delta == 0 or not use_color:
        colored = f"{code:>5}"
    else:
        color = _ANSI_GREEN if delta > 0 else _ANSI_RED
        colored = f"{color}{code:>5}{_ANSI_RESET}"
    delta_text = f"{delta:+d}" if delta else "  = "
    return colored, f"{delta_text:>4}"


def main(argv: list[str]) -> int:
    args = parse_args(argv)

    node = str(args.node) if args.node else None
    if node is None:
        adapters = candidate_adapters()
        if not adapters:
            print("no CP2112 adapter found", file=sys.stderr)
            return 1
        node = adapters[0][0]

    i2c = Cp2112I2c(node)
    if not i2c.available():
        print(f"i2c: cannot open {node}", file=sys.stderr)
        return 1

    if args.channel == "all":
        enabled_channels = list(range(args.channels))
    elif args.channel in (None, 0):
        enabled_channels = None
    else:
        enabled_channels = [args.channel]

    try:
        configure_channels(i2c, args.address, enabled_channels)
    except CommandRejected as exc:
        print(f"i2c: could not configure channels: {exc}", file=sys.stderr)
        return 1

    # Reads cycle through the enabled channels in order on their own; this
    # just labels each incoming value with the channel it must be.
    channel_cycle = itertools.cycle(enabled_channels) if enabled_channels else itertools.repeat(None)

    use_color = args.watch and sys.stdout.isatty()
    # --watch redraws its rows in place rather than scrolling, so the eye
    # stays on one spot instead of chasing new lines down the screen — but
    # only when there is a real cursor to move; a redirect gets the plain
    # scrolling log instead, since "move up N lines" means nothing there.
    in_place = args.watch and sys.stdout.isatty()
    last: dict[int | None, int] = {}
    previous_line_count = 0

    if enabled_channels is None:
        label = "channel 0 (CH0-only manual mode)"
    elif args.channel == "all":
        label = f"all channels 0-{args.channels - 1} (AUTO sequence)"
    else:
        label = f"channel {args.channel} (AUTO sequence)"
    print(f"reading 0x{args.address:02x} on {node}, {label}")
    header = f"  {'timestamp':<8}"
    if enabled_channels is not None and len(enabled_channels) > 1:
        header += "  ch"
    header += f"  {'raw':<9} {'code':>5}  {'pct':>5}"
    if args.watch:
        header += "  delta"
    print(header)

    rows_per_cycle = len(enabled_channels) if enabled_channels else 1
    count = 0
    try:
        while args.count is None or count < args.count:
            timestamp = time.strftime("%H:%M:%S")
            lines = []
            for _ in range(rows_per_cycle):
                channel = next(channel_cycle)
                try:
                    result = i2c.command("read", {"address": args.address, "length": args.length})
                    raw = bytes.fromhex(result["data_hex"].replace(" ", ""))
                    code, pct = decode(raw)
                    code_text, delta_text = format_delta(code, last.get(channel), use_color)
                    last[channel] = code
                    line = f"  {timestamp}"
                    if enabled_channels is not None and len(enabled_channels) > 1:
                        line += f"  {channel:>2}"
                    line += f"  {result['data_hex']:<9} {code_text}  {pct:5.1f}%"
                    if args.watch:
                        line += f"  {delta_text}"
                    lines.append(line)
                except CommandRejected as exc:
                    lines.append(f"  {timestamp}  error: {exc}")
            if in_place:
                if previous_line_count:
                    sys.stdout.write(f"\033[{previous_line_count}A")
                for line in lines:
                    sys.stdout.write(f"\033[K{line}\n")
                sys.stdout.flush()
                previous_line_count = len(lines)
            else:
                for line in lines:
                    print(line)
            count += 1
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        i2c.close()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
