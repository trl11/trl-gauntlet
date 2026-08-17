"""Session gates, the failure marker, and the check that the route was right."""

from __future__ import annotations

from typing import Any

import pytest
from gauntlet_sdk import AnomalyLog, IterationOutcome
from suite import runner
from suite.profile import TidLan7430Profile
from suite.telemetry import TelemetryState
from tests.conftest import RecordingSink


def _outcome(success: bool = True, **metrics: Any) -> IterationOutcome:
    body: dict[str, Any] = {
        "link": {"up": 1},
        "throughput": {"mean_tx_mbps": 940.0, "mean_rx_mbps": 940.0},
        "anomalies_total": 0,
    }
    body.update(metrics)
    return IterationOutcome(success=success, metrics=body)


def _state(anomalies: AnomalyLog) -> runner._State:
    return runner._State(anomalies=anomalies, telemetry=TelemetryState())


def test_a_healthy_session_passes() -> None:
    assert runner._evaluate([_outcome()], TidLan7430Profile()) == (True, "")


def test_a_session_with_no_ticks_fails() -> None:
    passed, reason = runner._evaluate([], TidLan7430Profile())

    assert not passed
    assert "no ticks" in reason


def test_a_session_that_never_measured_anything_fails() -> None:
    passed, reason = runner._evaluate([_outcome(success=False)], TidLan7430Profile())

    assert not passed
    assert "no tick produced" in reason


def test_throughput_below_the_floor_fails() -> None:
    profile = TidLan7430Profile()
    profile.pass_criteria.min_avg_tx_mbps = 900.0
    slow = _outcome(throughput={"mean_tx_mbps": 400.0, "mean_rx_mbps": 940.0})

    passed, reason = runner._evaluate([slow], profile)

    assert not passed
    assert "average transmit" in reason


def test_udp_loss_above_the_ceiling_fails() -> None:
    profile = TidLan7430Profile()
    profile.pass_criteria.max_udp_loss_pct = 1.0
    lossy = _outcome(udp={"loss_pct": 4.0})

    passed, reason = runner._evaluate([lossy], profile)

    assert not passed
    assert "UDP loss" in reason


def test_a_changed_otp_fails_only_when_the_profile_asks_it_to() -> None:
    profile = TidLan7430Profile()
    flipped = _outcome(otp_matches_baseline=0)

    assert runner._evaluate([flipped], profile) == (True, "")

    profile.pass_criteria.require_otp_stable = True
    passed, reason = runner._evaluate([flipped], profile)

    assert not passed
    assert "OTP image" in reason


def test_a_link_down_at_the_end_fails_only_when_the_profile_asks_it_to() -> None:
    profile = TidLan7430Profile()
    dead = _outcome(link={"up": 0})

    assert runner._evaluate([dead], profile) == (True, "")

    profile.pass_criteria.require_link_at_end = True
    passed, reason = runner._evaluate([dead], profile)

    assert not passed
    assert "link was down" in reason


def test_too_many_anomalies_fails() -> None:
    profile = TidLan7430Profile()
    profile.pass_criteria.max_anomalies = 5

    passed, reason = runner._evaluate([_outcome(anomalies_total=9)], profile)

    assert not passed
    assert "exceeds budget" in reason


def test_the_first_failing_tick_is_reported() -> None:
    outcomes = [_outcome(), _outcome(), _outcome(success=False), _outcome()]

    assert runner._first_failure(outcomes) == 2


def test_a_session_that_never_failed_reports_minus_one() -> None:
    assert runner._first_failure([_outcome(), _outcome()]) == -1


def test_traffic_that_did_not_cross_the_interface_is_recorded(anomalies: AnomalyLog, sink: RecordingSink) -> None:
    profile = TidLan7430Profile()
    measurements = {"tcp_tx": {"mbps": 900.0}, "tcp_rx": {"mbps": 900.0}, "udp": {"mbps": 0.0}}
    # iperf3 says it moved about a gigabyte; the interface carried a kilobyte.
    counters = {"bytes_step": 1_000}

    runner._check_traffic_crossed_part(_state(anomalies), measurements, counters, profile, iteration=3)

    assert "topology/traffic_bypassed_interface" in sink.kinds()


def test_traffic_that_did_cross_the_interface_is_not_recorded(anomalies: AnomalyLog, sink: RecordingSink) -> None:
    profile = TidLan7430Profile()
    measurements = {"tcp_tx": {"mbps": 900.0}, "tcp_rx": {"mbps": 900.0}, "udp": {"mbps": 0.0}}
    moved = int(900e6 * profile.iperf.stream_s * 2 / 8)

    runner._check_traffic_crossed_part(_state(anomalies), measurements, {"bytes_step": moved}, profile, iteration=3)

    assert sink.records == []


def test_the_route_check_is_skipped_when_the_counters_were_unreadable(
    anomalies: AnomalyLog, sink: RecordingSink
) -> None:
    profile = TidLan7430Profile()
    measurements = {"tcp_tx": {"mbps": 900.0}}

    runner._check_traffic_crossed_part(_state(anomalies), measurements, {}, profile, iteration=3)

    assert sink.records == []


def test_the_route_check_is_skipped_when_nothing_was_measured(anomalies: AnomalyLog, sink: RecordingSink) -> None:
    profile = TidLan7430Profile()

    runner._check_traffic_crossed_part(_state(anomalies), {}, {"bytes_step": 4_000}, profile, iteration=3)

    assert sink.records == []


@pytest.mark.parametrize("field", ["duration_s", "sample_period_s"])
def test_the_spec_reads_its_cadence_from_the_profile(field: str) -> None:
    profile = TidLan7430Profile()
    setattr(profile, field, 12.5)

    reader = runner.SPEC.duration_seconds if field == "duration_s" else runner.SPEC.sample_period_seconds

    assert reader(profile) == 12.5


def test_the_login_comes_from_the_profile_not_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A run started from the app inherits the server's environment, which is
    # why the profile has to win over whatever happens to be exported.
    monkeypatch.setenv("GAUNTLET_SSH_USER", "root")

    target = runner._ssh_target(TidLan7430Profile(), "192.0.2.10")

    assert target.user == "trl"
    assert target.host == "192.0.2.10"


def test_a_profile_naming_a_key_uses_it(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    key = tmp_path / "bench_key"
    key.write_text("")
    monkeypatch.setenv("GAUNTLET_SSH_KEY", "/somewhere/else")

    target = runner._ssh_target(TidLan7430Profile(ssh_user="operator", ssh_key_path=str(key)), "192.0.2.10")

    assert target.user == "operator"
    assert target.key_path == str(key)


def test_a_profile_naming_no_key_falls_back_to_the_bundled_one() -> None:
    target = runner._ssh_target(TidLan7430Profile(), "192.0.2.10")

    assert target.key_path.endswith("trl-engineering-keys/saver/id_ed_saver_eng_key")
