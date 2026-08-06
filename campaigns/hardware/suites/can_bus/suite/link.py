"""Driving the CAN counter link.

The unit runs a small sender that emits an incrementing counter as the payload
of each frame. The lab host receives from its socketcan interface on a
background thread and queues the decoded values, so bus timing is decoupled
from the sample cadence.
"""

from __future__ import annotations

import contextlib
import queue
import struct
import threading
from typing import Any

from gauntlet_sdk.remote import RemoteError, run, shell_quote

# Sent on the unit; stdlib only so it runs anywhere python3 exists.
SENDER_SCRIPT = '''\
#!/usr/bin/env python3
"""Emit an incrementing counter as CAN frames until killed."""
import socket
import struct
import sys
import time

iface, can_id, rate_hz = sys.argv[1], int(sys.argv[2], 0), float(sys.argv[3])
sock = socket.socket(socket.AF_CAN, socket.SOCK_RAW, socket.CAN_RAW)
sock.bind((iface,))
interval = 1.0 / rate_hz if rate_hz > 0 else 0.0
counter = 0
while True:
    payload = struct.pack("<Q", counter)
    sock.send(struct.pack("=IB3x8s", can_id, len(payload), payload))
    counter += 1
    if interval:
        time.sleep(interval)
'''


class LinkError(RuntimeError):
    """The CAN link could not be configured or opened."""


def _can() -> Any:
    try:
        import can
    except ImportError as exc:
        raise LinkError("CAN support needs python-can: pip install python-can") from exc
    return can


def configure_unit_interface(client: Any, iface: str, bitrate: int, *, timeout: float) -> None:
    """Bring the unit's CAN interface up at the configured bitrate."""
    for command in (
        f"sudo -n ip link set {shell_quote(iface)} down",
        f"sudo -n ip link set {shell_quote(iface)} type can bitrate {int(bitrate)}",
        f"sudo -n ip link set {shell_quote(iface)} up",
    ):
        result = run(client, command, timeout=timeout)
        # Taking an already-down interface down is not a failure.
        if not result.ok and "down" not in command:
            raise RemoteError(f"configuring {iface} ({command}): {result.output}")


def stop_services(client: Any, units: list[str], *, timeout: float) -> list[str]:
    """Stop units that would otherwise share the bus. Returns those stopped."""
    stopped = []
    for unit in units:
        if run(client, f"systemctl is-active --quiet {shell_quote(unit)}", timeout=timeout).ok:
            run(client, f"sudo -n systemctl stop {shell_quote(unit)}", timeout=timeout)
            stopped.append(unit)
    return stopped


def start_services(client: Any, units: list[str], *, timeout: float) -> None:
    """Restart units stopped for the run."""
    for unit in units:
        with contextlib.suppress(Exception):
            run(client, f"sudo -n systemctl start {shell_quote(unit)}", timeout=timeout)


def install_sender(client: Any, install_dir: str, *, timeout: float) -> str:
    """Copy the sender onto the unit and return its remote path."""
    remote_path = f"{install_dir.rstrip('/')}/can_sender.py"
    command = (
        f"mkdir -p {shell_quote(install_dir)} && printf '%s' {shell_quote(SENDER_SCRIPT)} > {shell_quote(remote_path)}"
    )
    result = run(client, command, timeout=timeout)
    if not result.ok:
        raise RemoteError(f"installing sender to {remote_path}: {result.output}")
    return remote_path


def start_sender(client: Any, remote_path: str, iface: str, can_id: int, rate_hz: float) -> Any:
    """Launch the sender on the unit and return its channel.

    The channel stays open for the run; closing it is what stops the sender.
    """
    transport = client.get_transport()
    if transport is None:
        raise RemoteError("ssh transport is not open")
    channel = transport.open_session()
    channel.exec_command(f"sudo -n python3 {shell_quote(remote_path)} {shell_quote(iface)} {hex(can_id)} {rate_hz:g}")
    return channel


class CounterReceiver:
    """Reads counter frames from a socketcan interface in the background."""

    def __init__(self, iface: str, arbitration_id: int) -> None:
        can = _can()
        try:
            self._bus = can.interface.Bus(channel=iface, interface="socketcan")
        except Exception as exc:
            raise LinkError(f"opening socketcan {iface}: {exc}") from exc
        self._arbitration_id = arbitration_id
        self.values: queue.Queue[int] = queue.Queue()
        self.decode_errors = 0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True, name="can-recv")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        with contextlib.suppress(Exception):
            self._bus.shutdown()

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                message = self._bus.recv(timeout=0.5)
            except Exception:
                self.decode_errors += 1
                return
            if message is None or message.arbitration_id != self._arbitration_id:
                continue
            data = bytes(message.data)
            if len(data) < 8:
                self.decode_errors += 1
                continue
            self.values.put(struct.unpack("<Q", data[:8])[0])
