"""Reports whether the host's udev rules reached the instruments plugged in.

``rig/99-gauntlet-instruments.rules`` is the declaration. This reads the
vendor ids out of it rather than repeating them, so a rule added there is
checked here without touching this file, then reports the owner and mode of
every usbfs node those rules cover.

Stdlib only, and no import of ``gauntlet``: it runs on whichever host the
instruments are plugged into, which is not necessarily one that has the
project's virtualenv.

    python3 tools/bench/udev_check.py    # exit 1 unless every node is writable
"""

from __future__ import annotations

import grp
import os
import pwd
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
RULES = ROOT / "rig" / "99-gauntlet-instruments.rules"
USB_DEVICES = Path("/sys/bus/usb/devices")

_VENDOR = re.compile(r'ATTRS\{idVendor\}=="([0-9a-fA-F]{4})"')


def claimed_vendors() -> set[str]:
    """The USB vendor ids the rules file claims, lowercased."""
    return {vendor.lower() for vendor in _VENDOR.findall(RULES.read_text())}


def node_of(device: Path) -> Path | None:
    """The usbfs node for one ``/sys/bus/usb/devices`` entry."""
    try:
        bus = read(device, "busnum")
        number = read(device, "devnum")
    except OSError:
        return None
    if not bus or not number:
        return None
    return Path(f"/dev/bus/usb/{int(bus):03d}/{int(number):03d}")


def owner_of(node: Path) -> str:
    """``user:group mode`` for a node, as ``ls -l`` would put it."""
    info = node.stat()
    try:
        user = pwd.getpwuid(info.st_uid).pw_name
    except KeyError:
        user = str(info.st_uid)
    try:
        group = grp.getgrgid(info.st_gid).gr_name
    except KeyError:
        group = str(info.st_gid)
    return f"{user}:{group} {info.st_mode & 0o777:o}"


def read(device: Path, name: str) -> str:
    """One ``/sys`` attribute, empty when the device does not carry it."""
    try:
        return (device / name).read_text().strip()
    except OSError:
        return ""


def main() -> int:
    vendors = claimed_vendors()
    if not vendors:
        print(f"{RULES} claims no vendor id")
        return 1
    if not USB_DEVICES.is_dir():
        print(f"no {USB_DEVICES}: this host has no USB bus to check")
        return 1

    found = 0
    unreachable = 0
    for device in sorted(USB_DEVICES.iterdir()):
        if read(device, "idVendor").lower() not in vendors:
            continue
        node = node_of(device)
        if node is None or not node.exists():
            continue
        found += 1
        serial = read(device, "serial")
        vendor_product = f"{read(device, 'idVendor')}:{read(device, 'idProduct')}"
        # The question the rule exists to settle is whether this process can
        # talk to the device, so ask that rather than reading the mode bits.
        writable = os.access(node, os.W_OK)
        unreachable += 0 if writable else 1
        print(
            f"  {vendor_product}"
            + (f"  serial {serial}" if serial else "")
            + f"  {node}  {owner_of(node)}  "
            + ("OK" if writable else "NOT WRITABLE by this user")
        )

    if not found:
        print("  no instrument the rules cover is plugged in")
        return 0
    if unreachable:
        print(f"\n{unreachable} of {found} unreachable. Check the user is in the group the rule names.")
    return 1 if unreachable else 0


if __name__ == "__main__":
    sys.exit(main())
