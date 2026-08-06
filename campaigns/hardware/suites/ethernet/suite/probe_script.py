#!/usr/bin/env python3
"""Ethernet throughput probe — runs on the unit under test.

Installed onto the unit at setup and invoked once per tick over SSH.

One invocation performs a single speed test against the lab-side TCP
throughput server:

1. *Transmit* — open a connection, send a ``U <nbytes>`` header, push
   ``nbytes`` of payload, wait for the server's one-byte ACK (so the
   timing covers bytes the server actually drained), and divide by the
   elapsed time to get the UUT → lab rate.
2. *Receive* — open a second connection, send a ``D <nbytes>`` header,
   read ``nbytes`` back, and divide to get the lab → UUT rate.

Emits exactly one JSON line on stdout::

    {"tx_mbps": <float|null>, "rx_mbps": <float|null>, "error": <str|null>}

Throughput is megabits per second (bytes * 8 / seconds / 1e6).
Diagnostics go to stderr. Stdlib only — no third-party imports — so the
probe runs on any unit with python3 and needs no install step of its
own.

Argv (positional, all required):
    lab_host  server_port  transfer_bytes  socket_timeout_s
"""

from __future__ import annotations

import json
import socket
import sys
import time

_CHUNK = 256 * 1024


def _measure_tx(lab_host: str, port: int, nbytes: int, timeout: float) -> float:
    """Time an upload of ``nbytes`` to the lab sink; return Mbps."""
    sock = socket.create_connection((lab_host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(f"U {nbytes}\n".encode())
        payload = bytes(_CHUNK)
        sent = 0
        started = time.monotonic()
        while sent < nbytes:
            n = min(_CHUNK, nbytes - sent)
            sock.sendall(payload[:n])
            sent += n
        # The server replies with a single ACK byte once it has drained
        # every byte — without it the timer would stop while the last
        # socket-buffer of data is still in flight.
        if sock.recv(1) != b"\x06":
            raise OSError("server did not acknowledge upload")
        elapsed = time.monotonic() - started
    finally:
        sock.close()
    if elapsed <= 0:
        raise OSError("upload completed in non-positive time")
    return (sent * 8) / elapsed / 1_000_000


def _measure_rx(lab_host: str, port: int, nbytes: int, timeout: float) -> float:
    """Time a download of ``nbytes`` from the lab source; return Mbps."""
    sock = socket.create_connection((lab_host, port), timeout=timeout)
    try:
        sock.settimeout(timeout)
        sock.sendall(f"D {nbytes}\n".encode())
        got = 0
        started = time.monotonic()
        while got < nbytes:
            chunk = sock.recv(_CHUNK)
            if not chunk:
                break
            got += len(chunk)
        elapsed = time.monotonic() - started
    finally:
        sock.close()
    if got < nbytes:
        raise OSError(f"download truncated ({got}/{nbytes} bytes)")
    if elapsed <= 0:
        raise OSError("download completed in non-positive time")
    return (got * 8) / elapsed / 1_000_000


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print(f"usage: {argv[0]} lab_host server_port transfer_bytes socket_timeout_s", file=sys.stderr)
        return 2
    lab_host = argv[1]
    port = int(argv[2])
    nbytes = int(argv[3])
    timeout = float(argv[4])

    result: dict[str, object] = {"tx_mbps": None, "rx_mbps": None, "error": None}
    try:
        result["tx_mbps"] = round(_measure_tx(lab_host, port, nbytes, timeout), 3)
        result["rx_mbps"] = round(_measure_rx(lab_host, port, nbytes, timeout), 3)
    except (OSError, ValueError) as exc:
        result["error"] = f"{type(exc).__name__}: {exc}"
        print(f"probe error: {result['error']}", file=sys.stderr)

    # Single JSON line on stdout — the runner reads the last line.
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
