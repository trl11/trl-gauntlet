#!/usr/bin/env python3
"""Put PMU3 on the PIC18F26K83 and prove it is running, before a dose run.

Bench tool, not a suite. ``tid_pic18f26k83`` measures a part that is already
executing firmware; this is what gets it there, and what a beam-line session
should end with a green result from before anyone opens the shutter.

Three ways to use it:

    tools/bench/pic_flash.py                    # flash the image in the tree, verify
    tools/bench/pic_flash.py --build            # build from the submodule first
    tools/bench/pic_flash.py --image x.hex      # flash one from somewhere else
    tools/bench/pic_flash.py --verify-only      # just ask the board what it is running

The first is the one a rig wants, and it is the default for that reason.
Building needs XC8 and the device pack, which is several gigabytes of
Microchip installer; flashing needs only MPLAB X's ``ipecmd``, and the image
committed at ``campaigns/radiation_tid/suites/tid_pic18f26k83/firmware/
pmu3_firmware.hex`` is 130KB. So a bench machine builds and commits, and a rig
only flashes.

The suite reads the version back off the board on every run and records it
against that image's SHA-256, so a part running something else is reported
rather than measured quietly.

**A failed flash leaves the part erased.** ipecmd erases before it programs,
so a programming error is not a no-op: the part is blank and will not answer
until a flash completes. The retry below exists because the PKoB4's USB
endpoint times out often enough to matter, and leaving a part erased is not an
acceptable place to stop.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIRMWARE_ROOT = REPO_ROOT / "extras" / "trl-pmu-firmware"
SUITE_ROOT = REPO_ROOT / "campaigns" / "radiation_tid" / "suites" / "tid_pic18f26k83"
IMAGE_DIR = FIRMWARE_ROOT / "dist" / "linux_cli" / "production"
SUITE_IMAGE = SUITE_ROOT / "firmware" / "pmu3_firmware.hex"

XC8_BIN = Path("/opt/microchip/xc8/v3.10/bin")


def firmware_make(target: str, *extra: str) -> int:
    """Run one target in the firmware tree with XC8 on PATH."""
    env = dict(os.environ)
    if XC8_BIN.is_dir():
        env["PATH"] = f"{XC8_BIN}:{env.get('PATH', '')}"
    return subprocess.call(["make", target, *extra], cwd=FIRMWARE_ROOT, env=env)


def newest_image() -> Path | None:
    """The most recently built versioned image, if there is one."""
    found = sorted(IMAGE_DIR.glob("pmu3_firmware-*.prod.hex"), key=lambda p: p.stat().st_mtime)
    return found[-1] if found else None


def committed_image() -> Path | None:
    """The image kept in the tree, which is what a bench flashes by default."""
    return SUITE_IMAGE if SUITE_IMAGE.is_file() else None


def flash(image: Path | None) -> int:
    """Probe, then program, retrying once through a programmer reset.

    The PKoB4 answers USB and then fails its bulk transfer often enough that a
    single attempt is not a useful answer. ``make tool-reset`` renumerates it,
    which clears the condition every time it has been seen here.
    """
    extra = [f"FLASH_HEX={image}"] if image is not None else []
    if firmware_make("flash", *extra) == 0:
        return 0
    print("\nflash failed; resetting the programmer and trying once more\n", file=sys.stderr)
    firmware_make("tool-reset")
    return firmware_make("flash", *extra)


def verify(device: str, baud: int, timeout_s: float) -> int:
    """Ask the board what it is running, through the suite's own console.

    Imported rather than reimplemented so this tool and the suite agree about
    what a working console is: if this passes and the suite then cannot reach
    the board, the difference is not the transport.
    """
    sys.path.insert(0, str(SUITE_ROOT))
    from suite.board import Board
    from suite.console import Console, ConsoleError, describe_device, resolve_device

    try:
        resolved = resolve_device(device, baud, timeout_s)
    except ConsoleError as exc:
        print(f"console: {exc}", file=sys.stderr)
        return 1
    try:
        board = Board(Console(resolved, baud))
    except ConsoleError as exc:
        print(f"console: {exc}", file=sys.stderr)
        return 1
    try:
        line = board.ask("v", "fw=", timeout_s)
    except ConsoleError as exc:
        print(f"{resolved}: the board did not answer: {exc}", file=sys.stderr)
        print(
            "A part that flashed and does not talk is usually the console wiring: the\n"
            "cable's TX goes to the PIC's RC7, its RX to RC5, and its ground to the board's.",
            file=sys.stderr,
        )
        return 1
    finally:
        board.close()
    print(f"{resolved} ({describe_device(resolved) or 'unknown'}): {line}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--image",
        type=Path,
        default=None,
        help="a .hex to flash. Defaults to the one committed beside the suite; --build takes the submodule's",
    )
    parser.add_argument("--build", action="store_true", help="build from the submodule instead of using the tree's")
    parser.add_argument("--verify-only", action="store_true", help="skip the flash, just read the version back")
    parser.add_argument("--device", default="auto", help="console tty, or `auto` to find the board")
    parser.add_argument("--baud", type=int, default=115200)
    parser.add_argument("--timeout-s", type=float, default=2.0)
    args = parser.parse_args(argv)

    if not args.verify_only:
        image = args.image
        if image is None and args.build:
            print("==> building the firmware")
            if firmware_make("build") != 0:
                print("build failed; pass --image to flash one built elsewhere", file=sys.stderr)
                return 1
            image = newest_image()
            if image is None:
                print(f"the build produced no image in {IMAGE_DIR}", file=sys.stderr)
                return 1
        elif image is None:
            image = committed_image()
            if image is None:
                print(f"no image at {SUITE_IMAGE}; pass --image or --build", file=sys.stderr)
                return 1
        elif not image.is_file():
            print(f"no such image: {image}", file=sys.stderr)
            return 1
        print(f"==> flashing {image}")
        if flash(image) != 0:
            print(
                "\nflash failed twice. The part is erased and will not run until one\n"
                "succeeds. Replug the programmer's USB and try again; if it keeps\n"
                "timing out, put it on the machine directly rather than through a hub.",
                file=sys.stderr,
            )
            return 1

    print("==> asking the board what it is running")
    return verify(args.device, args.baud, args.timeout_s)


if __name__ == "__main__":
    sys.exit(main())
