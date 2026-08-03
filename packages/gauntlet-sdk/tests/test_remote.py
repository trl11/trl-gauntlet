"""SSH access to the unit under test, driven against a fake paramiko."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import paramiko
import pytest

from gauntlet_sdk import RemoteError, RemoteTarget
from gauntlet_sdk.remote import (
    CommandResult,
    capture_host_facts,
    connect,
    is_alive,
    open_ssh,
    run,
    sample_host_metrics,
    shell_quote,
)


class FakeChannel:
    """One remote command: canned output, and a delay before it finishes."""

    def __init__(self, stdout=b"", stderr=b"", exit_status=0, *, polls_before_exit=0, raise_on_exec=None):
        self._stdout = [stdout] if stdout else []
        self._stderr = [stderr] if stderr else []
        self._exit_status = exit_status
        self._polls_before_exit = polls_before_exit
        self._raise_on_exec = raise_on_exec
        self.command = ""
        self.closed = False

    def exec_command(self, command):
        if self._raise_on_exec is not None:
            raise self._raise_on_exec
        self.command = command

    def recv_ready(self):
        return bool(self._stdout)

    def recv(self, _size):
        return self._stdout.pop(0)

    def recv_stderr_ready(self):
        return bool(self._stderr)

    def recv_stderr(self, _size):
        return self._stderr.pop(0)

    def exit_status_ready(self):
        if self._polls_before_exit > 0:
            self._polls_before_exit -= 1
            return False
        return True

    def recv_exit_status(self):
        return self._exit_status

    def close(self):
        self.closed = True


class FakeTransport:
    def __init__(self, channel):
        self._channel = channel
        self.keepalive = None

    def open_session(self):
        return self._channel

    def set_keepalive(self, seconds):
        self.keepalive = seconds


class FakeClient:
    def __init__(self, channel=None, transport=None):
        self._transport = transport if channel is None else FakeTransport(channel)
        self.closed = False

    def get_transport(self):
        return self._transport

    def close(self):
        self.closed = True


def _client(**kwargs) -> FakeClient:
    return FakeClient(FakeChannel(**kwargs))


class TestParamikoMissing:
    def test_the_error_names_the_extra_that_installs_it(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "paramiko", None)

        with pytest.raises(RemoteError, match=r"gauntlet-sdk\[remote\]"):
            run(FakeClient(FakeChannel()), "true")


class TestRemoteTarget:
    def test_an_explicit_host_wins_over_the_environment(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_TARGET", "from-env")

        assert RemoteTarget.from_env(host="explicit").host == "explicit"

    def test_the_host_comes_from_the_environment_otherwise(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_TARGET", " unit-3 ")

        assert RemoteTarget.from_env().host == "unit-3"

    def test_no_host_anywhere_is_an_error_naming_the_variable(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_TARGET", raising=False)

        with pytest.raises(RemoteError, match="GAUNTLET_TARGET"):
            RemoteTarget.from_env()

    def test_the_user_defaults_to_root(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_SSH_USER", raising=False)

        assert RemoteTarget.from_env(host="unit-3").user == "root"

    def test_the_user_comes_from_the_environment(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_SSH_USER", "operator")

        assert RemoteTarget.from_env(host="unit-3").user == "operator"

    def test_a_configured_key_is_expanded(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_SSH_KEY", "~/keys/unit")

        assert RemoteTarget.from_env(host="unit-3").key_path == os.path.expanduser("~/keys/unit")

    def test_an_unconfigured_key_probes_the_usual_locations(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GAUNTLET_SSH_KEY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        (tmp_path / ".ssh").mkdir()
        (tmp_path / ".ssh" / "id_rsa").write_text("key")

        assert RemoteTarget.from_env(host="unit-3").key_path == str(tmp_path / ".ssh" / "id_rsa")

    def test_the_first_candidate_is_the_fallback_when_none_exist(self, monkeypatch, tmp_path):
        monkeypatch.delenv("GAUNTLET_SSH_KEY", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))

        assert RemoteTarget.from_env(host="unit-3").key_path == str(tmp_path / ".ssh" / "id_ed25519")


class TestConnect:
    @pytest.fixture
    def key(self, tmp_path):
        path = tmp_path / "id_ed25519"
        path.write_text("private key")
        return path

    def _fake_paramiko(self, monkeypatch, client, connect_error=None):
        class SSHClient:
            def __init__(self):
                self.policy = None
                self.kwargs = {}
                self.closed = False

            def set_missing_host_key_policy(self, policy):
                self.policy = policy

            def connect(self, **kwargs):
                if connect_error is not None:
                    raise connect_error
                self.kwargs = kwargs

            def get_transport(self):
                return client.get_transport()

            def close(self):
                self.closed = True

        module = SimpleNamespace(
            SSHClient=SSHClient,
            AutoAddPolicy=lambda: "auto-add",
            SSHException=paramiko.SSHException,
        )
        monkeypatch.setattr("gauntlet_sdk.remote._paramiko", lambda: module)
        return module

    def test_a_missing_key_is_reported_before_dialling(self, tmp_path):
        target = RemoteTarget(host="unit-3", key_path=str(tmp_path / "absent"))

        with pytest.raises(RemoteError, match="ssh key not found"):
            connect(target)

    def test_a_connected_client_gets_a_keepalive(self, monkeypatch, key):
        client = _client()
        self._fake_paramiko(monkeypatch, client)

        opened = connect(RemoteTarget(host="unit-3", user="operator", key_path=str(key)), keepalive_s=17)

        assert opened.kwargs["hostname"] == "unit-3"
        assert opened.kwargs["username"] == "operator"
        assert opened.kwargs["key_filename"] == str(key)
        assert opened.kwargs["allow_agent"] is False
        assert opened.policy == "auto-add"
        assert client.get_transport().keepalive == 17

    def test_a_refused_connection_closes_the_client_and_raises(self, monkeypatch, key):
        self._fake_paramiko(monkeypatch, _client(), connect_error=OSError("connection refused"))

        with pytest.raises(RemoteError, match="ssh to root@unit-3: connection refused"):
            connect(RemoteTarget(host="unit-3", key_path=str(key)))

    def test_an_ssh_protocol_failure_is_a_remote_error(self, monkeypatch, key):
        self._fake_paramiko(monkeypatch, _client(), connect_error=paramiko.SSHException("bad banner"))

        with pytest.raises(RemoteError, match="bad banner"):
            connect(RemoteTarget(host="unit-3", key_path=str(key)))

    def test_a_client_without_a_transport_is_still_returned(self, monkeypatch, key):
        self._fake_paramiko(monkeypatch, FakeClient(transport=None))

        assert connect(RemoteTarget(host="unit-3", key_path=str(key))) is not None

    def test_open_ssh_closes_the_client_afterwards(self, monkeypatch, key):
        self._fake_paramiko(monkeypatch, _client())

        with open_ssh(RemoteTarget(host="unit-3", key_path=str(key))) as client:
            assert not client.closed

        assert client.closed

    def test_open_ssh_closes_the_client_when_the_body_raises(self, monkeypatch, key):
        self._fake_paramiko(monkeypatch, _client())
        captured = {}

        with pytest.raises(RuntimeError), open_ssh(RemoteTarget(host="unit-3", key_path=str(key))) as client:
            captured["client"] = client
            raise RuntimeError("boom")

        assert captured["client"].closed


class TestRun:
    def test_output_and_exit_status_come_back_together(self):
        client = _client(stdout=b"hello\n", stderr=b"noise\n", exit_status=0)

        result = run(client, "echo hello")

        assert result.exit_status == 0
        assert result.stdout == "hello\n"
        assert result.stderr == "noise\n"
        assert result.ok
        assert result.duration_s >= 0.0

    def test_the_command_reaches_the_channel(self):
        channel = FakeChannel(stdout=b"ok\n")
        run(FakeClient(channel), "uname -r")

        assert channel.command == "uname -r"

    def test_a_non_zero_exit_is_not_ok(self):
        assert not run(_client(exit_status=1), "false").ok

    def test_output_falls_back_to_stderr_when_stdout_is_empty(self):
        result = run(_client(stderr=b"  permission denied  \n"), "cat /root/x")

        assert result.output == "permission denied"

    def test_output_prefers_stdout(self):
        assert run(_client(stdout=b" yes \n", stderr=b"noise"), "x").output == "yes"

    def test_undecodable_bytes_do_not_break_the_result(self):
        assert run(_client(stdout=b"\xff\xfe"), "x").stdout == "��"

    def test_a_channel_that_reports_ready_but_returns_nothing_stops_draining(self):
        channel = FakeChannel(stdout=b"")
        channel.recv_ready = lambda: True
        channel.recv = lambda _size: b""
        channel.recv_stderr_ready = lambda: False

        assert run(FakeClient(channel), "x").stdout == ""

    def test_output_written_before_the_exit_status_is_still_collected(self):
        client = _client(stdout=b"slow\n", polls_before_exit=2)

        assert run(client, "x").stdout == "slow\n"

    def test_the_channel_is_closed_afterwards(self):
        channel = FakeChannel(stdout=b"ok\n")
        run(FakeClient(channel), "x")

        assert channel.closed

    def test_a_command_that_never_finishes_times_out(self):
        channel = FakeChannel(polls_before_exit=10_000)

        with pytest.raises(TimeoutError, match=r"timed out after 0\.1s"):
            run(FakeClient(channel), "sleep 60", timeout=0.1)

        assert channel.closed

    def test_a_closed_transport_is_a_remote_error(self):
        with pytest.raises(RemoteError, match="transport is not open"):
            run(FakeClient(transport=None), "x")

    def test_an_ssh_failure_mid_command_is_a_remote_error(self):
        channel = FakeChannel(raise_on_exec=paramiko.SSHException("channel closed"))

        with pytest.raises(RemoteError, match="running remote command: channel closed"):
            run(FakeClient(channel), "x")


class TestCommandResult:
    def test_output_is_empty_when_the_command_printed_nothing(self):
        assert CommandResult(exit_status=0, stdout="", stderr="", duration_s=0.0).output == ""


class TestShellQuote:
    def test_a_plain_value_is_wrapped_in_single_quotes(self):
        assert shell_quote("unit-3") == "'unit-3'"

    def test_an_embedded_quote_is_escaped(self):
        assert shell_quote("it's") == "'it'\\''s'"


class TestCaptureHostFacts:
    def _responses(self, monkeypatch, mapping):
        def _fake_run(_client, command, **_kwargs):
            if command not in mapping:
                raise RemoteError("no such probe")
            output, status = mapping[command]
            return CommandResult(exit_status=status, stdout=output, stderr="", duration_s=0.01)

        monkeypatch.setattr("gauntlet_sdk.remote.run", _fake_run)

    def test_each_probe_that_answers_becomes_a_fact(self, monkeypatch):
        self._responses(monkeypatch, {"hostname": ("unit-3\n", 0), "uname -r": ("6.8.0\n", 0)})

        facts = capture_host_facts(FakeClient())

        assert facts == {"hostname": "unit-3", "kernel": "6.8.0"}

    def test_a_probe_that_fails_is_omitted_rather_than_fatal(self, monkeypatch):
        self._responses(monkeypatch, {"hostname": ("unit-3\n", 1)})

        assert capture_host_facts(FakeClient()) == {}

    def test_a_probe_that_times_out_is_omitted(self, monkeypatch):
        def _fake_run(_client, _command, **_kwargs):
            raise TimeoutError("slow")

        monkeypatch.setattr("gauntlet_sdk.remote.run", _fake_run)

        assert capture_host_facts(FakeClient()) == {}

    def test_only_the_first_line_is_kept(self, monkeypatch):
        self._responses(monkeypatch, {"hostname": ("unit-3\nextra\n", 0)})

        assert capture_host_facts(FakeClient())["hostname"] == "unit-3"

    def test_a_long_value_is_truncated(self, monkeypatch):
        self._responses(monkeypatch, {"hostname": ("x" * 500, 0)})

        assert len(capture_host_facts(FakeClient())["hostname"]) == 200


class TestSampleHostMetrics:
    def _output(self, monkeypatch, stdout, status=0):
        def _fake_run(_client, _command, **_kwargs):
            return CommandResult(exit_status=status, stdout=stdout, stderr="", duration_s=0.01)

        monkeypatch.setattr("gauntlet_sdk.remote.run", _fake_run)

    def test_load_memory_and_disk_are_parsed(self, monkeypatch):
        self._output(
            monkeypatch,
            "0.52 0.41 0.36 1/210 4242\n---\nMemTotal: 8000\nMemAvailable: 2000\n---\n/dev/sda1 100 60 40 61% /\n",
        )

        metrics = sample_host_metrics(FakeClient())

        assert metrics["load_1m"] == 0.52
        assert metrics["memory_total_kb"] == 8000
        assert metrics["memory_available_kb"] == 2000
        assert metrics["memory_used_pct"] == 75.0
        assert metrics["disk_used_pct"] == 61.0

    def test_memory_without_an_available_figure_reports_only_the_total(self, monkeypatch):
        self._output(monkeypatch, "0.5 x x\n---\nMemTotal: 8000\n---\n")

        metrics = sample_host_metrics(FakeClient())

        assert metrics["memory_total_kb"] == 8000
        assert "memory_used_pct" not in metrics

    def test_unparseable_sections_are_skipped_rather_than_fatal(self, monkeypatch):
        self._output(monkeypatch, "notanumber\n---\nMemTotal: lots\n---\nshort line\n")

        assert sample_host_metrics(FakeClient()) == {}

    def test_a_failed_command_samples_nothing(self, monkeypatch):
        self._output(monkeypatch, "0.5 x x\n", status=1)

        assert sample_host_metrics(FakeClient()) == {}

    def test_a_timeout_samples_nothing(self, monkeypatch):
        def _fake_run(_client, _command, **_kwargs):
            raise TimeoutError("slow")

        monkeypatch.setattr("gauntlet_sdk.remote.run", _fake_run)

        assert sample_host_metrics(FakeClient()) == {}


class TestIsAlive:
    def test_a_unit_that_answers_is_alive(self, monkeypatch):
        monkeypatch.setattr("gauntlet_sdk.remote.connect", lambda *a, **k: _client(exit_status=0))

        assert is_alive(RemoteTarget(host="unit-3")) is True

    def test_a_unit_whose_command_fails_is_not_alive(self, monkeypatch):
        monkeypatch.setattr("gauntlet_sdk.remote.connect", lambda *a, **k: _client(exit_status=1))

        assert is_alive(RemoteTarget(host="unit-3")) is False

    def test_a_unit_that_cannot_be_reached_is_not_alive(self, monkeypatch):
        def _refuse(*_args, **_kwargs):
            raise RemoteError("connection refused")

        monkeypatch.setattr("gauntlet_sdk.remote.connect", _refuse)

        assert is_alive(RemoteTarget(host="unit-3")) is False
