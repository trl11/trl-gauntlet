"""Reads everything about the LAN7430 that dose could move. Runs on the unit.

Installed onto the host holding the controller and invoked once per tick with
a JSON configuration on argv, printing one JSON object on stdout. Standard
library only: the unit is a bench Raspberry Pi and nothing is installed on it
beyond `ethtool` and `iperf3`.

Every probe is best-effort. A probe that fails records its reason under
``errors`` and leaves its section out, so one unreadable file cannot cost the
tick every other reading it would have carried.

Nothing here writes to the part. The OTP read is a read: OTP is
one-time-programmable and a write would be unrecoverable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

SYS_NET = Path("/sys/class/net")
SYS_PCI = Path("/sys/bus/pci/devices")

# A non-interactive SSH login on Debian gets a PATH without the sbin
# directories, so `ethtool` is not found by name even where it is installed.
# sudo substitutes its own secure_path and so never saw this, which left the
# privileged probes working while the unprivileged ones reported exit 127.
SBIN_DIRECTORIES = ("/usr/local/sbin", "/usr/sbin", "/sbin")

# Counters worth carrying every tick. The kernel exposes more, but these are
# the ones a degrading PHY or MAC moves.
STATISTIC_NAMES = (
    "collisions",
    "rx_bytes",
    "rx_crc_errors",
    "rx_dropped",
    "rx_errors",
    "rx_fifo_errors",
    "rx_frame_errors",
    "rx_length_errors",
    "rx_missed_errors",
    "rx_over_errors",
    "rx_packets",
    "tx_aborted_errors",
    "tx_bytes",
    "tx_carrier_errors",
    "tx_dropped",
    "tx_errors",
    "tx_fifo_errors",
    "tx_packets",
    "tx_window_errors",
)

DMESG_STAMP = re.compile(r"^\[\s*(\d+\.\d+)\]\s*(.*)$")


def read_text(path: Path) -> str:
    """One sysfs file, stripped, or an empty string if it cannot be read."""
    try:
        return path.read_text().strip()
    except OSError:
        return ""


def read_int(path: Path) -> int | None:
    """One sysfs file as an integer, or None when it is absent or not a number."""
    text = read_text(path)
    try:
        return int(text)
    except ValueError:
        return None


def resolve_tool(name: str) -> str:
    """Absolute path to a system tool, searching the sbin directories too.

    Falls back to the bare name so a tool that is genuinely absent still fails
    through the command itself rather than here.
    """
    search_path = os.pathsep.join((os.environ.get("PATH", os.defpath), *SBIN_DIRECTORIES))
    return shutil.which(name, path=search_path) or name


def shell(command: list[str], *, timeout: float = 20.0) -> tuple[int, str, str]:
    """Run a command, never raising. Returns exit status, stdout and stderr.

    Decoded with ``surrogateescape`` so the register and OTP dumps, which are
    binary, survive the round trip back to bytes intact.
    """
    try:
        done = subprocess.run(
            [resolve_tool(command[0]), *command[1:]],
            capture_output=True,
            check=False,
            encoding="utf-8",
            errors="surrogateescape",
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def shell_privileged(command: list[str], *, timeout: float = 20.0) -> tuple[int, str, str]:
    """Run a command through passwordless sudo, falling back to running it plain.

    Register and OTP reads need root. A bench without passwordless sudo still
    gets whatever the command yields unprivileged rather than nothing.
    """
    status, out, err = shell(["sudo", "-n", *command], timeout=timeout)
    if status == 0:
        return status, out, err
    return shell(command, timeout=timeout)


def collect_link(interface: str) -> dict[str, object]:
    """Link state as the kernel currently sees it."""
    base = SYS_NET / interface
    speed = read_int(base / "speed")
    return {
        "address": read_text(base / "address"),
        "carrier": read_int(base / "carrier"),
        "carrier_changes": read_int(base / "carrier_changes"),
        "carrier_down_count": read_int(base / "carrier_down_count"),
        "duplex": read_text(base / "duplex"),
        "mtu": read_int(base / "mtu"),
        "operstate": read_text(base / "operstate"),
        # A down link reports -1, which is not a speed.
        "speed_mbps": speed if speed is not None and speed > 0 else None,
    }


def collect_statistics(interface: str) -> dict[str, int]:
    """Kernel per-interface counters, as raw cumulative totals."""
    base = SYS_NET / interface / "statistics"
    found = {}
    for name in STATISTIC_NAMES:
        value = read_int(base / name)
        if value is not None:
            found[name] = value
    return found


def collect_ethtool_statistics(interface: str) -> tuple[dict[str, int], str]:
    """Driver-private counters from `ethtool -S`."""
    status, out, err = shell(["ethtool", "-S", interface])
    if status != 0:
        return {}, (err.strip() or out.strip() or f"ethtool -S exited {status}")[:300]
    found = {}
    for line in out.splitlines():
        name, sep, raw = line.partition(":")
        if not sep:
            continue
        try:
            found[name.strip()] = int(raw.strip())
        except ValueError:
            continue
    return found, ""


def collect_driver(interface: str) -> tuple[dict[str, str], str]:
    """Driver identity from `ethtool -i`, including the PCI address."""
    status, out, err = shell(["ethtool", "-i", interface])
    if status != 0:
        return {}, (err.strip() or out.strip() or f"ethtool -i exited {status}")[:300]
    found = {}
    for line in out.splitlines():
        name, sep, raw = line.partition(":")
        if sep:
            found[name.strip().replace("-", "_")] = raw.strip()
    return found, ""


def resolve_pci_slot(interface: str, configured: str) -> str:
    """The controller's PCI address, from the profile or from sysfs."""
    if configured:
        return configured
    device = (SYS_NET / interface / "device").resolve()
    return device.name if device.exists() else ""


def collect_aer(slot_path: Path) -> dict[str, int]:
    """PCIe Advanced Error Reporting counters for the device and its port.

    Correctable errors climbing is often the first thing dose does to a PCIe
    link, well before throughput moves.
    """
    found: dict[str, int] = {}
    for filename in ("aer_dev_correctable", "aer_dev_fatal", "aer_dev_nonfatal"):
        text = read_text(slot_path / filename)
        if not text:
            continue
        prefix = filename.replace("aer_dev_", "")
        for line in text.splitlines():
            parts = line.split()
            if len(parts) != 2:
                continue
            try:
                found[f"{prefix}.{parts[0]}"] = int(parts[1])
            except ValueError:
                continue
    return found


def collect_pcie(slot: str) -> dict[str, object]:
    """PCIe link state and error counters for the controller."""
    if not slot:
        return {}
    slot_path = SYS_PCI / slot
    if not slot_path.exists():
        return {"slot": slot, "present": False}
    return {
        "aer": collect_aer(slot_path),
        "current_link_speed": read_text(slot_path / "current_link_speed"),
        "current_link_width": read_int(slot_path / "current_link_width"),
        "max_link_speed": read_text(slot_path / "max_link_speed"),
        "max_link_width": read_int(slot_path / "max_link_width"),
        "present": True,
        "slot": slot,
    }


def collect_otp(interface: str, length: int) -> dict[str, object]:
    """Read the controller's OTP and hash it.

    `ethtool -e` on this driver returns the non-volatile image the MAC address
    is taken from. The hash is what the tick-to-tick comparison uses; the hex
    is carried so a changed image can be diffed byte by byte after the run.
    """
    status, out, err = shell_privileged(["ethtool", "-e", interface, "offset", "0", "length", str(length), "raw", "on"])
    if status != 0:
        return {"error": (err.strip() or out.strip() or f"ethtool -e exited {status}")[:300]}
    # `raw on` writes the bytes to stdout, which arrive here through a text
    # pipe, so they are recovered from the surrogate-escaped string.
    data = out.encode("utf-8", "surrogateescape")
    if not data:
        return {"error": "ethtool -e returned no data"}
    return {"bytes": len(data), "hex": data.hex(), "sha256": hashlib.sha256(data).hexdigest()}


def collect_registers(interface: str) -> dict[str, object]:
    """Hash the controller's register dump.

    A configuration register that changes on its own is a bit flip, and the
    hash is what makes that visible without knowing the register map.
    """
    status, out, err = shell_privileged(["ethtool", "-d", interface, "raw", "on"])
    if status != 0:
        return {"error": (err.strip() or out.strip() or f"ethtool -d exited {status}")[:300]}
    data = out.encode("utf-8", "surrogateescape")
    if not data:
        return {"error": "ethtool -d returned no data"}
    return {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def collect_temperatures(slot: str) -> dict[str, float]:
    """Every thermal zone the host exposes, plus any sensor on the controller."""
    found: dict[str, float] = {}
    for zone in sorted(Path("/sys/class/thermal").glob("thermal_zone*")):
        milli = read_int(zone / "temp")
        if milli is None:
            continue
        found[read_text(zone / "type") or zone.name] = round(milli / 1000.0, 2)
    if slot:
        for sensor in sorted((SYS_PCI / slot).glob("hwmon/hwmon*/temp*_input")):
            milli = read_int(sensor)
            if milli is not None:
                found[f"{slot}.{sensor.name}"] = round(milli / 1000.0, 2)
    return found


def collect_dmesg(patterns: list[str], since: float, max_lines: int) -> dict[str, object]:
    """Kernel lines matching any pattern that are newer than the last tick.

    The cursor is the kernel's own monotonic stamp, so a tick reports only
    what appeared since the previous one rather than the whole buffer.
    """
    status, out, err = shell_privileged(["dmesg"])
    if status != 0:
        return {"error": (err.strip() or f"dmesg exited {status}")[:300], "cursor": since, "lines": []}

    lowered = [pattern.lower() for pattern in patterns]
    cursor = since
    lines = []
    for line in out.splitlines():
        match = DMESG_STAMP.match(line)
        if not match:
            continue
        stamp = float(match.group(1))
        cursor = max(cursor, stamp)
        if stamp <= since:
            continue
        body = match.group(2)
        if any(pattern in body.lower() for pattern in lowered):
            lines.append({"at_s": stamp, "text": body[:400]})
    return {"cursor": cursor, "lines": lines[-max_lines:], "total": len(lines)}


def main() -> int:
    """Collect one sample and print it as JSON."""
    config = json.loads(sys.argv[1]) if len(sys.argv) > 1 else {}
    interface = str(config.get("interface", "eth1"))
    errors: dict[str, str] = {}

    if not (SYS_NET / interface).exists():
        print(json.dumps({"present": False, "interface": interface, "errors": {"interface": "not present in sysfs"}}))
        return 0

    driver, driver_error = collect_driver(interface)
    if driver_error:
        errors["driver"] = driver_error
    ethtool_statistics, ethtool_error = collect_ethtool_statistics(interface)
    if ethtool_error:
        errors["ethtool_stats"] = ethtool_error

    slot = resolve_pci_slot(interface, str(config.get("pci_slot", "")))
    sample: dict[str, object] = {
        "driver": driver,
        "ethtool_stats": ethtool_statistics,
        "interface": interface,
        "link": collect_link(interface),
        "pcie": collect_pcie(slot),
        "present": True,
        "statistics": collect_statistics(interface),
        "temperature_c": collect_temperatures(slot),
    }

    if config.get("otp_enabled", True):
        otp = collect_otp(interface, int(config.get("otp_length", 512)))
        if otp.get("error"):
            errors["otp"] = str(otp["error"])
        sample["otp"] = otp

    if config.get("registers_enabled", True):
        registers = collect_registers(interface)
        if registers.get("error"):
            errors["registers"] = str(registers["error"])
        sample["registers"] = registers

    if config.get("dmesg_enabled", True):
        dmesg = collect_dmesg(
            list(config.get("dmesg_patterns") or []),
            float(config.get("dmesg_cursor", 0.0)),
            int(config.get("dmesg_max_lines", 40)),
        )
        if dmesg.get("error"):
            errors["dmesg"] = str(dmesg["error"])
        sample["dmesg"] = dmesg

    sample["errors"] = errors
    print(json.dumps(sample))
    return 0


if __name__ == "__main__":
    sys.exit(main())
