"""Folding collector samples into metrics and anomalies."""

from __future__ import annotations

import copy
from typing import Any

from gauntlet_sdk import AnomalyLog
from suite import mock
from suite.profile import TidLan7430Profile
from suite.telemetry import TelemetryState, analyse, establish_baseline
from tests.conftest import RecordingSink


def _analyse(
    sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    iteration: int = 1,
) -> dict[str, Any]:
    return analyse(sample, state, profile, iteration, anomalies)


def test_a_healthy_sample_raises_nothing(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    metrics = _analyse(baseline_sample, state, profile, anomalies)

    assert sink.records == []
    assert metrics["link"]["up"] == 1
    assert metrics["otp_matches_baseline"] == 1
    assert metrics["registers_match_baseline"] == 1


def test_the_baseline_absorbs_counters_the_host_accumulated_before_the_run(
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    # The mock host has logged errors before the session ever started.
    already = mock.sample(0, "eth1")
    already["statistics"]["rx_crc_errors"] = 9000
    fresh = TelemetryState()
    establish_baseline(already, fresh)

    metrics = _analyse(copy.deepcopy(already), fresh, profile, anomalies)

    assert "counters/rx_crc_errors" not in sink.kinds()
    assert metrics["counters"]["rx_crc_errors"] == 0


def test_a_counter_that_moves_since_the_last_tick_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    worse = copy.deepcopy(baseline_sample)
    worse["statistics"]["rx_crc_errors"] = 7

    metrics = _analyse(worse, state, profile, anomalies)

    assert "counters/rx_crc_errors" in sink.kinds()
    assert metrics["counters"]["rx_crc_errors"] == 7


def test_a_counter_that_resets_is_not_reported_as_negative(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    state.previous_statistics["rx_crc_errors"] = 500
    reloaded = copy.deepcopy(baseline_sample)
    reloaded["statistics"]["rx_crc_errors"] = 0

    metrics = _analyse(reloaded, state, profile, anomalies)

    assert "counters/rx_crc_errors" not in sink.kinds()
    assert metrics["counters"]["rx_crc_errors"] == 0


def test_byte_counters_that_wrap_still_report_what_the_interface_carried(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    # The part's byte counters are 32 bits, so at gigabit they wrap between
    # ticks. Read as a reset, the receive half vanishes and the traffic
    # cross-check reports the traffic went around the part.
    state.previous_statistics["rx_bytes"] = 2**32 - 100_000
    state.previous_statistics["tx_bytes"] = 1_000
    wrapped = copy.deepcopy(baseline_sample)
    wrapped["statistics"]["rx_bytes"] = 400_000
    wrapped["statistics"]["tx_bytes"] = 5_000

    metrics = _analyse(wrapped, state, profile, anomalies)

    assert metrics["counters"]["bytes_step"] == 500_000 + 4_000


def test_a_down_link_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    dead = copy.deepcopy(baseline_sample)
    dead["link"]["operstate"] = "down"
    dead["link"]["speed_mbps"] = None

    metrics = _analyse(dead, state, profile, anomalies)

    assert "link/down" in sink.kinds()
    assert metrics["link"]["up"] == 0


def test_a_link_that_falls_back_to_a_slower_speed_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    slow = copy.deepcopy(baseline_sample)
    slow["link"]["speed_mbps"] = 100

    _analyse(slow, state, profile, anomalies)

    assert "link/speed_degraded" in sink.kinds()


def test_a_changed_mac_is_recorded_because_it_comes_from_the_otp(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    flipped = copy.deepcopy(baseline_sample)
    flipped["link"]["address"] = "00:80:0f:1a:2b:3d"

    _analyse(flipped, state, profile, anomalies)

    assert "link/mac_changed" in sink.kinds()
    assert state.mac_changes == 1
    assert state.golden_mac == mock.BASELINE_MAC
    assert state.mac == "00:80:0f:1a:2b:3d"


def test_the_mac_the_part_reports_is_carried_in_every_tick(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
) -> None:
    metrics = _analyse(baseline_sample, state, profile, anomalies)

    assert metrics["link"]["address"] == mock.BASELINE_MAC


def test_the_baseline_records_the_mac_and_the_otp_image_it_was_taken_from(
    baseline_sample: dict[str, Any],
) -> None:
    fresh = TelemetryState()

    establish_baseline(baseline_sample, fresh)

    assert fresh.mac == fresh.golden_mac == mock.BASELINE_MAC
    assert fresh.otp_sha == fresh.golden_otp_sha == baseline_sample["otp"]["sha256"]


def test_a_changed_otp_image_is_recorded_and_counted(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    flipped = copy.deepcopy(baseline_sample)
    flipped["otp"]["sha256"] = "0" * 64

    metrics = _analyse(flipped, state, profile, anomalies)

    assert "otp/changed" in sink.kinds()
    assert state.otp_changes == 1
    assert state.otp_sha == "0" * 64
    assert metrics["otp_matches_baseline"] == 0


def test_an_otp_that_cannot_be_read_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    unreadable = copy.deepcopy(baseline_sample)
    unreadable["otp"] = {"error": "Operation not permitted"}

    metrics = _analyse(unreadable, state, profile, anomalies)

    assert "otp/unreadable" in sink.kinds()
    assert metrics["otp_matches_baseline"] == 0


def test_a_changed_register_dump_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    flipped = copy.deepcopy(baseline_sample)
    flipped["registers"]["sha256"] = "f" * 64

    _analyse(flipped, state, profile, anomalies)

    assert "registers/changed" in sink.kinds()
    assert state.register_changes == 1


def test_climbing_aer_counters_are_recorded_by_severity(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    noisy = copy.deepcopy(baseline_sample)
    noisy["pcie"]["aer"]["correctable.RxErr"] = 12
    noisy["pcie"]["aer"]["fatal.TOTAL_ERR_FATAL"] = 1

    metrics = _analyse(noisy, state, profile, anomalies)

    assert "pcie/aer_correctable" in sink.kinds()
    assert "pcie/aer_fatal" in sink.kinds()
    assert metrics["pcie"]["aer_steps_total"] == 13


def test_a_pcie_link_running_narrower_than_it_can_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    narrow = copy.deepcopy(baseline_sample)
    narrow["pcie"]["current_link_width"] = 1
    narrow["pcie"]["max_link_width"] = 4

    _analyse(narrow, state, profile, anomalies)

    assert "pcie/link_degraded" in sink.kinds()


def test_a_device_that_fell_off_the_bus_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    gone = copy.deepcopy(baseline_sample)
    gone["pcie"] = {"present": False, "slot": "0000:01:00.0"}

    _analyse(gone, state, profile, anomalies)

    assert "pcie/device_missing" in sink.kinds()


def test_an_interface_that_disappeared_is_recorded_and_ends_the_tick(
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    metrics = _analyse({"present": False, "interface": "eth1"}, state, profile, anomalies)

    assert "link/interface_missing" in sink.kinds()
    assert metrics == {"present": 0}


def test_kernel_lines_are_recorded_and_move_the_cursor(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    noisy = copy.deepcopy(baseline_sample)
    noisy["dmesg"] = {
        "cursor": 4321.5,
        "lines": [{"at_s": 4300.0, "text": "lan743x 0000:01:00.0 eth1: Link is Down"}],
        "total": 1,
    }

    metrics = _analyse(noisy, state, profile, anomalies)

    assert "kernel/message" in sink.kinds()
    assert metrics["kernel_lines"] == 1
    assert state.dmesg_cursor == 4321.5


def test_a_probe_the_collector_could_not_read_is_recorded(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
    sink: RecordingSink,
) -> None:
    partial = copy.deepcopy(baseline_sample)
    partial["errors"] = {"ethtool_stats": "ethtool: command not found"}

    _analyse(partial, state, profile, anomalies)

    assert "collector/ethtool_stats" in sink.kinds()


def test_bytes_carried_are_reported_so_the_run_can_check_the_route(
    baseline_sample: dict[str, Any],
    state: TelemetryState,
    profile: TidLan7430Profile,
    anomalies: AnomalyLog,
) -> None:
    busy = copy.deepcopy(baseline_sample)
    busy["statistics"]["rx_bytes"] = baseline_sample["statistics"]["rx_bytes"] + 1_000
    busy["statistics"]["tx_bytes"] = baseline_sample["statistics"]["tx_bytes"] + 2_000

    metrics = _analyse(busy, state, profile, anomalies)

    assert metrics["counters"]["bytes_step"] == 3_000
