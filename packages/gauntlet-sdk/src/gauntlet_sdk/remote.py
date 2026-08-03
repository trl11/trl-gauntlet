"""SSH access to a unit under test.

Requires the ``remote`` extra for paramiko. The unit under test is whatever
``GAUNTLET_TARGET`` names.
"""

from __future__ import annotations

import contextlib
import os
import time
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import paramiko


TARGET_VAR = "GAUNTLET_TARGET"
SSH_USER_VAR = "GAUNTLET_SSH_USER"
SSH_KEY_VAR = "GAUNTLET_SSH_KEY"

# Tried in order when no key is configured. The first existing file wins.
_KEY_CANDIDATES = (
    "~/.ssh/id_ed25519",
    "~/.ssh/id_ecdsa",
    "~/.ssh/id_rsa",
)


class RemoteError(RuntimeError):
    """The remote host could not be reached or a command could not be run."""


@dataclass(frozen=True)
class RemoteTarget:
    """Where the unit under test is and how to log into it."""

    host: str
    user: str = "root"
    key_path: str = ""

    @classmethod
    def from_env(cls, *, host: str | None = None) -> RemoteTarget:
        """Resolve from the environment. An explicit ``host`` takes precedence."""
        resolved = (host or os.environ.get(TARGET_VAR, "")).strip()
        if not resolved:
            raise RemoteError(f"no target host: pass --target or set {TARGET_VAR}")
        return cls(
            host=resolved,
            user=os.environ.get(SSH_USER_VAR, "").strip() or "root",
            key_path=_resolve_key(os.environ.get(SSH_KEY_VAR, "")),
        )


def _resolve_key(configured: str) -> str:
    """Expand the configured key, or probe the usual locations."""
    if configured:
        return os.path.expanduser(configured)
    for candidate in _KEY_CANDIDATES:
        expanded = os.path.expanduser(candidate)
        if os.path.isfile(expanded):
            return expanded
    return os.path.expanduser(_KEY_CANDIDATES[0])


def _paramiko() -> Any:
    try:
        import paramiko
    except ImportError as exc:
        raise RemoteError("SSH support needs paramiko: pip install 'gauntlet-sdk[remote]'") from exc
    return paramiko


def connect(target: RemoteTarget, *, timeout: float = 10.0, keepalive_s: int = 30) -> paramiko.SSHClient:
    """Open an SSH connection. The caller owns the client and must close it."""
    paramiko = _paramiko()
    if not os.path.isfile(target.key_path):
        raise RemoteError(f"ssh key not found at {target.key_path} — set {SSH_KEY_VAR} to an existing key")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=target.host,
            username=target.user,
            key_filename=target.key_path,
            timeout=timeout,
            allow_agent=False,
            look_for_keys=False,
        )
    except (paramiko.SSHException, OSError) as exc:
        client.close()
        raise RemoteError(f"ssh to {target.user}@{target.host}: {exc}") from exc

    transport = client.get_transport()
    if transport is not None:
        # Keeps the session open across idle periods between ticks.
        transport.set_keepalive(keepalive_s)
    return client


@contextlib.contextmanager
def open_ssh(target: RemoteTarget, *, timeout: float = 10.0) -> Iterator[paramiko.SSHClient]:
    """Connected client as a context manager, closed on exit."""
    client = connect(target, timeout=timeout)
    try:
        yield client
    finally:
        client.close()


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one remote command."""

    exit_status: int
    stdout: str
    stderr: str
    duration_s: float

    @property
    def ok(self) -> bool:
        return self.exit_status == 0

    @property
    def output(self) -> str:
        """Stdout, falling back to stderr when a tool writes there instead."""
        return self.stdout.strip() or self.stderr.strip()


def run(client: paramiko.SSHClient, command: str, *, timeout: float = 30.0) -> CommandResult:
    """Run a command and wait for it, enforcing a wall-clock timeout.

    Raises :class:`TimeoutError` on the deadline. paramiko's own ``timeout``
    covers socket reads only.
    """
    paramiko = _paramiko()
    transport = client.get_transport()
    if transport is None:
        raise RemoteError("ssh transport is not open")

    started = time.monotonic()
    channel = transport.open_session()
    try:
        channel.exec_command(command)
        deadline = started + max(float(timeout), 0.1)
        stdout, stderr = b"", b""
        while True:
            stdout += _drain(channel.recv_ready, channel.recv)
            stderr += _drain(channel.recv_stderr_ready, channel.recv_stderr)
            if channel.exit_status_ready():
                exit_status = channel.recv_exit_status()
                stdout += _drain(channel.recv_ready, channel.recv)
                stderr += _drain(channel.recv_stderr_ready, channel.recv_stderr)
                return CommandResult(
                    exit_status=exit_status,
                    stdout=stdout.decode("utf-8", errors="replace"),
                    stderr=stderr.decode("utf-8", errors="replace"),
                    duration_s=time.monotonic() - started,
                )
            if time.monotonic() >= deadline:
                raise TimeoutError(f"command timed out after {timeout:.1f}s: {command[:120]}")
            time.sleep(0.05)
    except paramiko.SSHException as exc:
        raise RemoteError(f"running remote command: {exc}") from exc
    finally:
        with contextlib.suppress(Exception):
            channel.close()


def _drain(ready: Any, read: Any, chunk_size: int = 4096) -> bytes:
    out = b""
    while ready():
        chunk = read(chunk_size)
        if not chunk:
            break
        out += chunk
    return out


def shell_quote(value: str) -> str:
    """Quote a string for safe inclusion in a remote shell command."""
    return "'" + value.replace("'", "'\\''") + "'"


def capture_host_facts(client: paramiko.SSHClient, *, timeout: float = 10.0) -> dict[str, str]:
    """Identify the unit under test.

    Each probe is best-effort; a field that cannot be read is omitted.
    """
    probes = {
        "hostname": "hostname",
        "kernel": "uname -r",
        "arch": "uname -m",
        "os": "cat /etc/os-release 2>/dev/null | grep ^PRETTY_NAME= | cut -d= -f2- | tr -d '\"'",
        "uptime_s": "cut -d' ' -f1 /proc/uptime",
        "cpu_model": "grep -m1 'model name' /proc/cpuinfo | cut -d: -f2- | sed 's/^ *//'",
        "memory_kb": "grep -m1 MemTotal /proc/meminfo | awk '{print $2}'",
    }
    facts: dict[str, str] = {}
    for name, command in probes.items():
        try:
            result = run(client, command, timeout=timeout)
        except (RemoteError, TimeoutError):
            continue
        if result.ok and result.output:
            facts[name] = result.output.splitlines()[0][:200]
    return facts


def sample_host_metrics(client: paramiko.SSHClient, *, timeout: float = 10.0) -> dict[str, float]:
    """One snapshot of load, memory, and disk on the unit under test.

    Returns only the values that parsed.
    """
    command = (
        "cat /proc/loadavg; echo '---'; "
        "grep -E '^(MemTotal|MemAvailable):' /proc/meminfo; echo '---'; "
        "df -k / | tail -1"
    )
    try:
        result = run(client, command, timeout=timeout)
    except (RemoteError, TimeoutError):
        return {}
    if not result.ok:
        return {}

    sections = result.stdout.split("---")
    metrics: dict[str, float] = {}
    if sections and sections[0].split():
        with contextlib.suppress(ValueError, IndexError):
            metrics["load_1m"] = float(sections[0].split()[0])
    if len(sections) > 1:
        memory = {}
        for line in sections[1].splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0].endswith(":"):
                with contextlib.suppress(ValueError):
                    memory[parts[0].rstrip(":")] = float(parts[1])
        total, available = memory.get("MemTotal"), memory.get("MemAvailable")
        if total:
            metrics["memory_total_kb"] = total
            if available is not None:
                metrics["memory_available_kb"] = available
                metrics["memory_used_pct"] = round(100.0 * (1.0 - available / total), 2)
    if len(sections) > 2:
        fields = sections[2].split()
        if len(fields) >= 5:
            with contextlib.suppress(ValueError):
                metrics["disk_used_pct"] = float(fields[4].rstrip("%"))
    return metrics


def is_alive(target: RemoteTarget, *, timeout: float = 5.0) -> bool:
    """Whether the unit accepts an SSH connection and runs a command."""
    try:
        with open_ssh(target, timeout=timeout) as client:
            return run(client, "true", timeout=timeout).ok
    except (RemoteError, TimeoutError, OSError):
        return False
