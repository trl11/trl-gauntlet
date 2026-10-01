"""Measures what a DI-2008 delivers while it scans continuously.

Run it with the Python that has Gauntlet's dependencies, on the host the units
are plugged into, with nothing else holding them (stop the service first)::

    python3 tools/bench/di2008_stream.py --seconds 60
    python3 tools/bench/di2008_stream.py --seconds 20 --concurrent

For each scan list of 1, 4 and 8 channels it reports the scan rate the unit
delivers, the rate it claims, the longest gap between packets and the bytes read
per second. ``--interrupt`` sends ``info 9`` and then ``stop`` mid-scan and
reports whether the unit answers and whether the stream ends.
"""

from __future__ import annotations

import argparse
import threading
import time

from gauntlet.instruments.di2008_daq import (
    candidate_serials,
    decode_scans,
    open_usb,
    slist_word,
    strip_echo,
)

CHANNEL_COUNTS = (1, 4, 8)
PACKET_BYTES = 64


def command(transport, line: str, timeout_ms: int = 150) -> str:
    """Send one command and return what the unit echoes."""
    drain(transport, 5)
    transport.write((line + "\r").encode("ascii"))
    out = bytearray()
    while chunk := transport.read(PACKET_BYTES, timeout_ms):
        out += chunk
    return out.decode("ascii", errors="replace").strip("\x00\r\n ")


def drain(transport, timeout_ms: int) -> int:
    """Discard whatever is waiting on bulk-IN and return how many bytes it was."""
    total = 0
    while chunk := transport.read(PACKET_BYTES, timeout_ms):
        total += len(chunk)
    return total


def load_scan_list(transport, channels: int, srate: int) -> float:
    """Load ``channels`` 10 V inputs and return the clock the unit reports."""
    command(transport, "stop", 80)
    drain(transport, 200)
    for slot in range(channels):
        command(transport, f"slist {slot} {slist_word(slot, '10v')}")
    command(transport, f"slist {channels} 65535")
    command(transport, f"srate {srate}")
    command(transport, "dec 1")
    command(transport, "ps 0")
    reply = command(transport, "info 9", 200)
    return float(reply.removeprefix("info 9 "))


def measure(serial: str, channels: int, seconds: float, srate: int) -> dict[str, float]:
    """Scan ``channels`` inputs for ``seconds`` and describe the stream."""
    transport = open_usb(serial)
    try:
        clock_hz = load_scan_list(transport, channels, srate)
        claimed = clock_hz / (srate * channels)
        drain(transport, 5)
        transport.write(b"start\r")
        buf = bytearray()
        started = time.monotonic()
        last = started
        longest_gap = 0.0
        while time.monotonic() - started < seconds:
            chunk = transport.read(PACKET_BYTES, 150)
            if chunk:
                longest_gap = max(longest_gap, time.monotonic() - last)
                last = time.monotonic()
                buf += chunk
        elapsed = time.monotonic() - started
        transport.write(b"stop\r")
        drain(transport, 200)
        scans = len(decode_scans(strip_echo(bytes(buf)), channels))
        return {
            "bytes_per_s": len(buf) / elapsed,
            "claimed_hz": claimed,
            "delivered_hz": scans / elapsed,
            "lost_scans": max(0.0, claimed * elapsed - scans),
            "longest_gap_s": longest_gap,
        }
    finally:
        transport.close()


def interrupt(serial: str, srate: int) -> None:
    """Send ``info 9`` then ``stop`` while a scan runs and report what happens."""
    transport = open_usb(serial)
    try:
        load_scan_list(transport, 8, srate)
        drain(transport, 5)
        transport.write(b"start\r")
        time.sleep(0.5)
        transport.write(b"info 9\r")
        time.sleep(0.3)
        seen = bytearray()
        while chunk := transport.read(PACKET_BYTES, 50):
            seen += chunk
            if len(seen) > 4096:
                break
        print(f"  after info 9 mid-scan: {len(seen)} bytes read, contains reply: {b'info 9' in seen}")
        transport.write(b"stop\r")
        drain(transport, 300)
        print(f"  bytes still arriving after stop: {drain(transport, 300)}")
    finally:
        transport.close()


def report(serial: str, seconds: float, srate: int) -> None:
    print(f"{serial}")
    for channels in CHANNEL_COUNTS:
        result = measure(serial, channels, seconds, srate)
        print(f"  {channels} ch: " + ", ".join(f"{key} {value:.3f}" for key, value in result.items()), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrent", action="store_true", help="scan every unit at once")
    parser.add_argument("--interrupt", action="store_true", help="send info 9 and stop mid-scan")
    parser.add_argument("--seconds", type=float, default=60.0)
    parser.add_argument("--srate", type=int, default=4)
    args = parser.parse_args()

    serials = candidate_serials()
    print(f"units: {serials}")
    if args.interrupt:
        for serial in serials:
            print(serial)
            interrupt(serial, args.srate)
    elif args.concurrent:
        threads = [threading.Thread(target=report, args=(serial, args.seconds, args.srate)) for serial in serials]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    else:
        for serial in serials:
            report(serial, args.seconds, args.srate)


if __name__ == "__main__":
    main()
