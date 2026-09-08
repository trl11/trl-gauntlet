"""What the part is running, recorded with the run that measured it.

A dose characterisation is about a particular image on a particular die, so a
run that cannot say which image it was is a run whose numbers cannot be
compared with anything. Two records are kept, and they are independent on
purpose:

*What the board says.* Console ``v`` reports the build the part is actually
executing. It is the authority, because it comes from the part under the beam.

*The image itself.* The ``.hex`` is kept once, in ``firmware/`` beside this
suite, because a part is programmed before a campaign rather than before a
run. Every run records its name and its SHA-256, so any run can be traced back
to the bytes that were on the part without carrying a copy of them.

The two are cross-checked. A part running something other than the image in
the tree is recorded as a mismatch, because the whole point of keeping the
image is that it says what ran.
"""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

# Console 'v' answers with the build, the register map version and the
# configuration, on one line: "fw=3.0.0-19f0af71 api=6 build=production".
_KEY_VERSION = "v"
_MARKER = "fw="

# Matches both the image kept here and the versioned one `make` leaves in the
# firmware tree.
_IMAGE_GLOB = "pmu3_firmware*.hex"

# The version the firmware reports over its console, as it sits in the image.
_IMAGE_VERSION_RE = re.compile(rb"fw=([0-9A-Za-z.\-_+]+)")

# The image this suite is run against, kept in the tree so a bench needs
# neither the firmware repository nor its toolchain to know what was flashed.
_SUITE_DIR = "firmware"

# Where a freshly built one lands, searched second so a bench that just built
# an image gets that rather than the committed one.
_SUBMODULE_DIST = Path("../../../../extras/trl-pmu-firmware/dist/linux_cli/production")
_DIR_ENV = "PMU3_FIRMWARE_DIR"


def identify(report_line: str) -> dict[str, str]:
    """The fields of a console ``v`` reply, by name."""
    fields: dict[str, str] = {}
    for token in report_line.split():
        key, sep, value = token.partition("=")
        if sep:
            fields[key] = value
    return fields


def version_key() -> str:
    """The console key that asks for the version."""
    return _KEY_VERSION


def version_marker() -> str:
    """What its reply is recognised by."""
    return _MARKER


def locate(configured: str, suite_dir: Path) -> Path | None:
    """The image this run is taken against, or ``None`` when there is none.

    A missing image is not an error. The board's own report is the authority
    and a bench that flashed from elsewhere still measures fine; what is lost
    is the cross-check, and that is said rather than raised.
    """
    if configured and configured != "auto":
        path = Path(configured)
        return path if path.is_file() else None
    roots = []
    override = os.environ.get(_DIR_ENV, "")
    if override:
        roots.append(Path(override))
    roots.append(suite_dir / _SUITE_DIR)
    roots.append(suite_dir / _SUBMODULE_DIST)
    for root in roots:
        found = sorted(root.glob(_IMAGE_GLOB), key=lambda p: p.stat().st_mtime if p.exists() else 0)
        if found:
            return found[-1]
    return None


def image_version(image: Path) -> str:
    """The version string built into the image, empty if it holds none.

    This is the same text the board answers ``v`` with, so the two compare
    directly. An image that cannot be read as Intel HEX yields nothing rather
    than raising: the cross-check is worth having and not worth a run for.
    """
    try:
        match = _IMAGE_VERSION_RE.search(_hex_payload(image))
    except (OSError, ValueError):
        return ""
    return match.group(1).decode("ascii") if match else ""


def _hex_payload(image: Path) -> bytes:
    """The data records of an Intel HEX file, concatenated.

    Address records are ignored. Nothing here is reconstructing a memory map --
    the only thing wanted is the bytes, to look for a string in.
    """
    payload = bytearray()
    for line in image.read_text().splitlines():
        record = line.strip()
        if not record.startswith(":") or len(record) < 11:
            continue
        count = int(record[1:3], 16)
        if int(record[7:9], 16) == 0:
            payload += bytes.fromhex(record[9 : 9 + count * 2])
    return bytes(payload)


def digest(path: Path) -> str:
    """The image's SHA-256, so the artifact can be identified on its own."""
    sha = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(65536), b""):
            sha.update(block)
    return sha.hexdigest()
