"""Driving iperf3 and reading what it reports back."""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest
from suite import iperf
from suite.profile import TidLan7430Profile

TCP_REPORT = {
    "end": {
        "sum_received": {"bits_per_second": 941_200_000.0, "seconds": 5.0},
        "sum_sent": {"bits_per_second": 942_000_000.0, "retransmits": 3},
    }
}

UDP_REPORT = {
    "end": {
        "sum": {
            "bits_per_second": 899_000_000.0,
            "jitter_ms": 0.042,
            "lost_packets": 12,
            "lost_percent": 0.0157,
            "packets": 76_400,
        }
    }
}


class _Completed:
    def __init__(self, stdout: str, stderr: str = "", returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def handle() -> iperf.ServerHandle:
    return iperf.ServerHandle(address="192.168.11.7", log_path="/tmp/x.log", pid_path="/tmp/x.pid", port=5201)


@pytest.fixture
def profile() -> TidLan7430Profile:
    return TidLan7430Profile()


def _answer_with(monkeypatch: pytest.MonkeyPatch, payload: Any, seen: list[list[str]] | None = None) -> None:
    """Make every iperf3 invocation return ``payload``."""

    def fake_run(command: list[str], **_kwargs: Any) -> _Completed:
        if seen is not None:
            seen.append(command)
        return _Completed(json.dumps(payload))

    monkeypatch.setattr(subprocess, "run", fake_run)


def test_a_tcp_direction_is_reported_in_mbps(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    _answer_with(monkeypatch, TCP_REPORT)

    result = iperf.measure_tcp(handle, profile, reverse=False)

    assert result["mbps"] == pytest.approx(941.2)
    assert result["retransmits"] == 3.0


def test_the_unit_to_lab_direction_asks_iperf3_to_reverse(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    seen: list[list[str]] = []
    _answer_with(monkeypatch, TCP_REPORT, seen)

    iperf.measure_tcp(handle, profile, reverse=True)
    iperf.measure_tcp(handle, profile, reverse=False)

    assert "--reverse" in seen[0]
    assert "--reverse" not in seen[1]


def test_the_client_is_pointed_at_the_controllers_own_address(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    seen: list[list[str]] = []
    _answer_with(monkeypatch, TCP_REPORT, seen)

    iperf.measure_tcp(handle, profile, reverse=False)

    assert "--client" in seen[0]
    assert seen[0][seen[0].index("--client") + 1] == "192.168.11.7"


def test_the_egress_address_is_not_pinned_unless_the_profile_says_so(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    seen: list[list[str]] = []
    _answer_with(monkeypatch, TCP_REPORT, seen)

    iperf.measure_tcp(handle, profile, reverse=False)

    assert "--bind" not in seen[0]


def test_the_egress_address_is_pinned_when_the_profile_names_one(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    profile.iperf.lab_address = "192.168.11.20"
    seen: list[list[str]] = []
    _answer_with(monkeypatch, TCP_REPORT, seen)

    iperf.measure_tcp(handle, profile, reverse=False)
    iperf.measure_udp(handle, profile)

    for command in seen:
        assert command[command.index("--bind") + 1] == "192.168.11.20"


def test_a_udp_pass_reports_loss_and_jitter(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    _answer_with(monkeypatch, UDP_REPORT)

    result = iperf.measure_udp(handle, profile)

    assert result["loss_pct"] == pytest.approx(0.0157)
    assert result["jitter_ms"] == pytest.approx(0.042)
    assert result["mbps"] == pytest.approx(899.0)


def test_an_error_iperf3_reports_becomes_an_iperf_error(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    _answer_with(monkeypatch, {"error": "unable to connect to server: Connection refused"})

    with pytest.raises(iperf.IperfError, match="Connection refused"):
        iperf.measure_tcp(handle, profile, reverse=False)


def test_output_that_is_not_json_becomes_an_iperf_error(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Completed("iperf3: not a report"))

    with pytest.raises(iperf.IperfError, match="not JSON"):
        iperf.measure_tcp(handle, profile, reverse=False)


def test_no_output_at_all_becomes_an_iperf_error(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    monkeypatch.setattr(subprocess, "run", lambda *_a, **_k: _Completed("", "killed"))

    with pytest.raises(iperf.IperfError, match="no output"):
        iperf.measure_tcp(handle, profile, reverse=False)


def test_a_missing_client_says_so_rather_than_raising_oserror(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    def missing(*_args: Any, **_kwargs: Any) -> None:
        raise FileNotFoundError("iperf3")

    monkeypatch.setattr(subprocess, "run", missing)

    with pytest.raises(iperf.IperfError, match="not installed on the lab host"):
        iperf.measure_tcp(handle, profile, reverse=False)


def test_a_client_that_hangs_is_given_up_on(
    monkeypatch: pytest.MonkeyPatch, handle: iperf.ServerHandle, profile: TidLan7430Profile
) -> None:
    def hang(*_args: Any, **_kwargs: Any) -> None:
        raise subprocess.TimeoutExpired(cmd="iperf3", timeout=120.0)

    monkeypatch.setattr(subprocess, "run", hang)

    with pytest.raises(iperf.IperfError, match="did not finish"):
        iperf.measure_tcp(handle, profile, reverse=False)


def test_the_server_binds_to_the_controllers_address(profile: TidLan7430Profile) -> None:
    commands: list[str] = []

    class FakeResult:
        ok = True
        output = ""

    def fake_run(_client: Any, command: str, **_kwargs: Any) -> FakeResult:
        commands.append(command)
        return FakeResult()

    import suite.iperf as module

    original = module.run
    module.run = fake_run  # type: ignore[assignment]
    try:
        handle = iperf.start_server(object(), profile, "192.168.11.7")
    finally:
        module.run = original  # type: ignore[assignment]

    assert handle.address == "192.168.11.7"
    assert any("--bind '192.168.11.7'" in command for command in commands)


def test_stopping_also_sweeps_the_port_in_case_the_pid_file_was_lost(
    profile: TidLan7430Profile, handle: iperf.ServerHandle
) -> None:
    """A server left behind by a crashed run holds the port and poisons the next one."""
    commands: list[str] = []

    class FakeResult:
        ok = True
        output = ""

    def fake_run(_client: Any, command: str, **_kwargs: Any) -> FakeResult:
        commands.append(command)
        return FakeResult()

    import suite.iperf as module

    original = module.run
    module.run = fake_run  # type: ignore[assignment]
    try:
        iperf.stop_server(object(), handle)
    finally:
        module.run = original  # type: ignore[assignment]

    assert "pkill -f" in commands[0]
    # Narrow enough that an iperf3 serving something else is left alone.
    assert "--bind 192.168.11.7 --port 5201" in commands[0]


def test_the_pid_recorded_is_the_servers_and_not_the_shells(profile: TidLan7430Profile) -> None:
    """`A && B & echo $!` records the wrong pid, and the server then restarts every tick."""
    commands: list[str] = []

    class FakeResult:
        ok = True
        output = ""

    def fake_run(_client: Any, command: str, **_kwargs: Any) -> FakeResult:
        commands.append(command)
        return FakeResult()

    import suite.iperf as module

    original = module.run
    module.run = fake_run  # type: ignore[assignment]
    try:
        iperf.start_server(object(), profile, "192.168.11.7")
    finally:
        module.run = original  # type: ignore[assignment]

    started = next(command for command in commands if "nohup iperf3" in command)
    # Only the server may be backgrounded, so `$!` names it. Without the
    # grouping the whole `mkdir && nohup ...` becomes one job instead.
    assert "{ nohup iperf3" in started
    assert started.rstrip().endswith("; }")
