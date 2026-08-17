"""Throughput measurement across the controller, driven with iperf3.

The server runs on the unit bound to the controller's own address and the
client runs lab-side. Binding the server that way is what forces the traffic
over the part under test: the unit's reply has to leave through the interface
the address belongs to, rather than the host's built-in one.

Directions are named from the unit's point of view, matching how the rest of
the campaign reads: ``tx`` is unit-to-lab and ``rx`` is lab-to-unit.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any

from gauntlet_sdk.remote import RemoteError, run, shell_quote

from suite.profile import TidLan7430Profile


class IperfError(RuntimeError):
    """A measurement could not be taken."""


@dataclass
class ServerHandle:
    """The iperf3 server running on the unit."""

    address: str
    log_path: str
    pid_path: str
    port: int


def client_available() -> bool:
    """Whether this host has the iperf3 client the measurement needs."""
    return shutil.which("iperf3") is not None


def start_server(client: Any, profile: TidLan7430Profile, address: str) -> ServerHandle:
    """Start the iperf3 server on the unit, replacing one already listening.

    Left running for the whole session rather than started per tick: a beam
    run takes thousands of ticks, and a fresh listener each time would spend
    most of the tick in setup.
    """
    handle = ServerHandle(
        address=address,
        log_path=f"{profile.install_dir.rstrip('/')}/iperf3-server.log",
        pid_path=f"{profile.install_dir.rstrip('/')}/iperf3-server.pid",
        port=profile.iperf.port,
    )
    stop_server(client, handle, timeout=profile.ssh_timeout_s)
    # The braces matter. `A && B & echo $!` backgrounds the whole `A && B` as
    # one job, so `$!` is that job rather than iperf3, and the pid recorded
    # dies immediately — which reads as a dead server and restarts it every
    # tick. Grouping backgrounds only the server, so `$!` is the server.
    command = (
        f"mkdir -p {shell_quote(profile.install_dir)} && "
        f"{{ nohup iperf3 --server --bind {shell_quote(address)} --port {handle.port} "
        f"> {shell_quote(handle.log_path)} 2>&1 & echo $! > {shell_quote(handle.pid_path)}; }}"
    )
    result = run(client, command, timeout=profile.ssh_timeout_s)
    if not result.ok:
        raise IperfError(f"starting iperf3 server on {address}:{handle.port}: {result.output}")
    return handle


def server_alive(client: Any, handle: ServerHandle, *, timeout: float = 30.0) -> bool:
    """Whether the server process recorded at setup is still running."""
    command = f"kill -0 $(cat {shell_quote(handle.pid_path)} 2>/dev/null) 2>/dev/null && echo alive || echo gone"
    try:
        result = run(client, command, timeout=timeout)
    except (RemoteError, TimeoutError, OSError):
        return False
    return result.stdout.strip() == "alive"


def stop_server(client: Any, handle: ServerHandle, *, timeout: float = 30.0) -> None:
    """Stop the server and forget its pid file. Never raises.

    The pid file is not enough on its own. A run that died without tearing
    down, or one whose pid file was lost, leaves a server holding the port;
    the next run's server then fails to bind and exits immediately, while the
    stale one keeps answering. The measurements still succeed, so nothing
    looks wrong except a server that reports itself dead every tick.

    So the port is swept too. The pattern names this address and port, which
    keeps it from touching an iperf3 serving something else on the unit.
    """
    pattern = f"iperf3 --server --bind {handle.address} --port {handle.port}"
    command = (
        f"if [ -f {shell_quote(handle.pid_path)} ]; then "
        f"kill $(cat {shell_quote(handle.pid_path)}) 2>/dev/null; "
        f"rm -f {shell_quote(handle.pid_path)}; fi; "
        f"pkill -f {shell_quote(pattern)} 2>/dev/null; true"
    )
    try:
        run(client, command, timeout=timeout)
    except (RemoteError, TimeoutError, OSError):
        return


def _run_client(arguments: list[str], timeout_s: float) -> dict[str, Any]:
    """Run one iperf3 client and return its parsed JSON report.

    iperf3 reports a refused connection as JSON with an ``error`` key and a
    non-zero status, so the body is parsed before the status is judged.
    """
    try:
        done = subprocess.run(
            ["iperf3", "--json", *arguments],
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        raise IperfError("iperf3 is not installed on the lab host") from exc
    except subprocess.TimeoutExpired as exc:
        raise IperfError(f"iperf3 client did not finish within {timeout_s:.0f}s") from exc
    except OSError as exc:
        raise IperfError(f"running iperf3: {exc}") from exc

    text = done.stdout.strip()
    if not text:
        raise IperfError(f"iperf3 produced no output: {done.stderr.strip()[-300:]}")
    try:
        report = dict(json.loads(text))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise IperfError(f"iperf3 output was not JSON: {text[:200]}") from exc
    if report.get("error"):
        raise IperfError(str(report["error"]))
    return report


def _base_arguments(handle: ServerHandle, profile: TidLan7430Profile) -> list[str]:
    """The flags every client run shares.

    ``--bind`` pins which of the lab host's addresses the traffic leaves from.
    It matters on a bench where the unit is reachable by more than one route,
    because without it the kernel picks, and the direction that gets measured
    is whichever one it picked.
    """
    arguments = ["--client", handle.address, "--port", str(handle.port)]
    if profile.iperf.lab_address:
        arguments += ["--bind", profile.iperf.lab_address]
    return arguments


def measure_tcp(handle: ServerHandle, profile: TidLan7430Profile, *, reverse: bool) -> dict[str, float]:
    """One TCP direction, in Mbps, with the retransmit count that went with it."""
    arguments = [
        *_base_arguments(handle, profile),
        "--time",
        str(profile.iperf.stream_s),
        "--omit",
        str(profile.iperf.omit_s),
        "--parallel",
        str(profile.iperf.parallel),
    ]
    if reverse:
        arguments.append("--reverse")
    report = _run_client(arguments, profile.iperf.client_timeout_s)

    end = report.get("end", {})
    received = end.get("sum_received", {})
    sent = end.get("sum_sent", {})
    bits = received.get("bits_per_second") or sent.get("bits_per_second") or 0.0
    return {
        "mbps": round(float(bits) / 1e6, 3),
        "retransmits": float(sent.get("retransmits") or 0.0),
        "seconds": round(float(received.get("seconds") or 0.0), 3),
    }


def measure_udp(handle: ServerHandle, profile: TidLan7430Profile) -> dict[str, float]:
    """A UDP pass, for the loss and jitter a degrading link shows first."""
    arguments = [
        *_base_arguments(handle, profile),
        "--udp",
        "--bitrate",
        profile.iperf.udp_bitrate,
        "--time",
        str(profile.iperf.udp_stream_s),
    ]
    report = _run_client(arguments, profile.iperf.client_timeout_s)

    summary = report.get("end", {}).get("sum", {})
    return {
        "jitter_ms": round(float(summary.get("jitter_ms") or 0.0), 4),
        "loss_pct": round(float(summary.get("lost_percent") or 0.0), 4),
        "lost_packets": float(summary.get("lost_packets") or 0.0),
        "mbps": round(float(summary.get("bits_per_second") or 0.0) / 1e6, 3),
        "packets": float(summary.get("packets") or 0.0),
    }
