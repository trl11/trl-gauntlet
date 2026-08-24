"""Keeping the unit's other interfaces from answering for the part's address."""

from __future__ import annotations

import pytest
from gauntlet_sdk.remote import CommandResult, RemoteError
from suite import arp


class FakeClient:
    """A unit that answers the commands arp.py sends, and remembers them."""

    def __init__(self, settings: dict[str, int], *, sudo_allowed: bool = True) -> None:
        self.commands: list[str] = []
        self.settings = dict(settings)
        self.sudo_allowed = sudo_allowed


def _run(client: FakeClient, command: str, *, timeout: float = 30.0) -> CommandResult:
    client.commands.append(command)
    if command.startswith("cat "):
        body = "".join(f"{client.settings[name]}\n" for name in arp.SETTINGS if name in client.settings)
        return CommandResult(exit_status=0, stdout=body, stderr="", duration_s=0.0)
    if command.startswith("sudo -n sysctl"):
        if not client.sudo_allowed:
            return CommandResult(exit_status=1, stdout="", stderr="sudo: a password is required", duration_s=0.0)
        client.settings = dict(arp.STRICT)
        return CommandResult(exit_status=0, stdout="", stderr="", duration_s=0.0)
    return CommandResult(exit_status=0, stdout="", stderr="", duration_s=0.0)


@pytest.fixture(autouse=True)
def fake_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(arp, "run", _run)


def test_the_permissive_defaults_are_not_strict() -> None:
    assert not arp.is_strict({"arp_announce": 0, "arp_ignore": 0})


def test_settings_at_least_as_strict_as_wanted_pass() -> None:
    assert arp.is_strict({"arp_announce": 2, "arp_ignore": 1})
    assert arp.is_strict({"arp_announce": 2, "arp_ignore": 8})


def test_settings_that_could_not_be_read_are_not_taken_for_strict() -> None:
    assert not arp.is_strict({})


def test_a_unit_that_answers_partially_reads_as_unknown() -> None:
    client = FakeClient({"arp_ignore": 1})

    assert arp.read(client, timeout=5.0) == {}


def test_enforcing_sets_both_and_flushes_the_neighbours() -> None:
    client = FakeClient({"arp_announce": 0, "arp_ignore": 0})

    after = arp.enforce(client, timeout=5.0)

    assert arp.is_strict(after)
    assert any("net.ipv4.conf.all.arp_ignore=1" in c for c in client.commands)
    assert any("net.ipv4.conf.all.arp_announce=2" in c for c in client.commands)
    assert any("ip neigh flush all" in c for c in client.commands)


def test_a_unit_that_refuses_sudo_raises_rather_than_reporting_success() -> None:
    client = FakeClient({"arp_announce": 0, "arp_ignore": 0}, sudo_allowed=False)

    with pytest.raises(RemoteError):
        arp.enforce(client, timeout=5.0)


def test_describe_reads_as_a_clause() -> None:
    assert arp.describe({"arp_announce": 0, "arp_ignore": 0}) == "arp_announce=0, arp_ignore=0"
    assert arp.describe({}) == "unreadable"
