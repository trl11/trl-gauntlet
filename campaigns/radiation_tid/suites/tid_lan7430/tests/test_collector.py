"""The collector's readers, against a sysfs tree built on disk.

Nothing here touches the real ``/sys``: the tree is written into a temporary
directory and the module is pointed at it, so the readers are exercised
against known input on any host.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from suite import collector_script


@pytest.fixture
def net_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A ``/sys/class/net`` holding one healthy gigabit interface."""
    interface = tmp_path / "eth1"
    (interface / "statistics").mkdir(parents=True)
    for name, value in (
        ("address", "00:80:0f:1a:2b:3c"),
        ("carrier", "1"),
        ("carrier_changes", "2"),
        ("duplex", "full"),
        ("mtu", "1500"),
        ("operstate", "up"),
        ("speed", "1000"),
    ):
        (interface / name).write_text(f"{value}\n")
    for name, value in (("rx_crc_errors", "4"), ("rx_packets", "1200"), ("tx_packets", "1100")):
        (interface / "statistics" / name).write_text(f"{value}\n")
    monkeypatch.setattr(collector_script, "SYS_NET", tmp_path)
    return tmp_path


def test_link_state_is_read_from_sysfs(net_root: Path) -> None:
    link = collector_script.collect_link("eth1")

    assert link["operstate"] == "up"
    assert link["speed_mbps"] == 1000
    assert link["duplex"] == "full"
    assert link["address"] == "00:80:0f:1a:2b:3c"
    assert link["carrier_changes"] == 2


def test_a_down_link_reports_no_speed_rather_than_minus_one(net_root: Path) -> None:
    (net_root / "eth1" / "speed").write_text("-1\n")

    assert collector_script.collect_link("eth1")["speed_mbps"] is None


def test_a_file_that_cannot_be_read_leaves_its_field_empty(net_root: Path) -> None:
    (net_root / "eth1" / "duplex").unlink()

    assert collector_script.collect_link("eth1")["duplex"] == ""


def test_only_the_named_counters_are_collected(net_root: Path) -> None:
    statistics = collector_script.collect_statistics("eth1")

    assert statistics == {"rx_crc_errors": 4, "rx_packets": 1200, "tx_packets": 1100}


def test_aer_counters_are_read_per_severity(tmp_path: Path) -> None:
    (tmp_path / "aer_dev_correctable").write_text("RxErr 12\nBadTLP 0\n")
    (tmp_path / "aer_dev_fatal").write_text("TOTAL_ERR_FATAL 1\n")

    counters = collector_script.collect_aer(tmp_path)

    assert counters["correctable.RxErr"] == 12
    assert counters["correctable.BadTLP"] == 0
    assert counters["fatal.TOTAL_ERR_FATAL"] == 1


def test_a_slot_with_no_aer_files_reads_as_empty(tmp_path: Path) -> None:
    assert collector_script.collect_aer(tmp_path) == {}


def test_a_device_that_is_gone_is_reported_as_absent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_script, "SYS_PCI", tmp_path)

    assert collector_script.collect_pcie("0000:01:00.0") == {"slot": "0000:01:00.0", "present": False}


def test_ethtool_statistics_are_parsed_into_integers(monkeypatch: pytest.MonkeyPatch) -> None:
    output = "NIC statistics:\n     RX FCS Errors: 7\n     TX Total Frames: 991\n     Some Text: n/a\n"
    monkeypatch.setattr(collector_script, "shell", lambda *_a, **_k: (0, output, ""))

    statistics, error = collector_script.collect_ethtool_statistics("eth1")

    assert statistics == {"RX FCS Errors": 7, "TX Total Frames": 991}
    assert error == ""


def test_a_missing_ethtool_is_reported_rather_than_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_script, "shell", lambda *_a, **_k: (127, "", "ethtool: not found"))

    statistics, error = collector_script.collect_ethtool_statistics("eth1")

    assert statistics == {}
    assert "not found" in error


def test_only_kernel_lines_newer_than_the_cursor_are_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    log = (
        "[    1.100000] lan743x 0000:01:00.0: probe\n"
        "[  500.500000] usb 1-1: new device\n"
        "[  900.250000] lan743x 0000:01:00.0 eth1: Link is Down\n"
    )
    monkeypatch.setattr(collector_script, "shell_privileged", lambda *_a, **_k: (0, log, ""))

    result = collector_script.collect_dmesg(["lan743x"], since=100.0, max_lines=40)

    assert [line["at_s"] for line in result["lines"]] == [900.25]
    assert result["cursor"] == 900.25


def test_the_cursor_advances_even_when_nothing_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_script, "shell_privileged", lambda *_a, **_k: (0, "[  700.0] usb 1-1: reset\n", ""))

    result = collector_script.collect_dmesg(["lan743x"], since=100.0, max_lines=40)

    assert result["lines"] == []
    assert result["cursor"] == 700.0


def test_a_dmesg_that_cannot_be_read_keeps_the_cursor_where_it_was(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_script, "shell_privileged", lambda *_a, **_k: (1, "", "read kernel buffer failed"))

    result = collector_script.collect_dmesg(["lan743x"], since=42.0, max_lines=40)

    assert result["cursor"] == 42.0
    assert "read kernel buffer failed" in str(result["error"])


def test_the_otp_read_is_hashed_and_never_written(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []

    def fake(command: list[str], **_kwargs: object) -> tuple[int, str, str]:
        commands.append(command)
        return 0, "\x00\x01\x02\x03", ""

    monkeypatch.setattr(collector_script, "shell_privileged", fake)

    result = collector_script.collect_otp("eth1", 512)

    assert result["bytes"] == 4
    assert result["sha256"]
    # `ethtool -e` reads. The write form is `-E`, and it must never appear.
    assert commands[0][:3] == ["ethtool", "-e", "eth1"]
    assert "-E" not in commands[0]


def test_an_otp_that_cannot_be_read_reports_why(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(collector_script, "shell_privileged", lambda *_a, **_k: (1, "", "Operation not permitted"))

    assert "Operation not permitted" in str(collector_script.collect_otp("eth1", 512)["error"])
