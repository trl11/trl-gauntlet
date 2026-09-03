"""What the probe results are judged against, and what the mock disk does."""

from __future__ import annotations

from typing import Any

import pytest
from gauntlet_sdk import IterationOutcome
from pydantic import ValidationError
from suite import mock
from suite.probe import DeviceState, evaluate, read_kernel_log
from suite.profile import Device, DmesgBlock, ProbeBlock, TidSsdProfile
from suite.runner import _evaluate

DEVICE = Device(name="nvme0", device="/dev/nvme0n1", test_path="/var/tmp/probe.bin")

HEALTHY = {
    "device_present": True,
    "write_mbps": 2400.0,
    "read_mbps": 3100.0,
    "verify_ok": True,
    "verify_expected_sha": "a" * 64,
    "verify_actual_sha": "a" * 64,
    "cache_drop_failed": False,
    "test_path_ok": True,
    "test_path_disk": "nvme0n1",
    "device_disk": "nvme0n1",
    "smart": {"available_spare": 100, "available_spare_threshold": 10, "media_errors": 0},
    "error": None,
}


def kinds(state: DeviceState, result: dict[str, Any], block: ProbeBlock | None = None) -> set[str]:
    found = evaluate(state, block or ProbeBlock(), DEVICE, result)
    return {f"{a.probe}/{a.kind}" for a in found}


class TestProfile:
    def test_an_unknown_driver_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TidSsdProfile(driver="sometimes")

    def test_a_negative_duration_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TidSsdProfile(duration_s=-1.0)

    def test_an_undeclared_block_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            TidSsdProfile(iperf={"stream_s": 5.0})

    def test_a_run_with_no_end_is_allowed(self) -> None:
        assert TidSsdProfile(duration_s=0).duration_s == 0


class TestVerdict:
    def test_a_run_that_never_measured_anything_fails(self) -> None:
        outcomes = [IterationOutcome(success=False, metrics={"anomalies_total": 3})]
        assert _evaluate(outcomes, TidSsdProfile()) == (False, "no tick produced a bandwidth measurement")

    def test_miscompares_over_the_budget_fail_and_name_the_device(self) -> None:
        metrics = {"anomalies_total": 1, "nvme0": {"verify_ok": False, "verify_failures": 4}}
        profile = TidSsdProfile(pass_criteria={"max_verify_failures": 3})
        ok, reason = _evaluate([IterationOutcome(success=True, metrics=metrics)], profile)
        assert not ok
        assert "nvme0: 4 miscompares" in reason

    def test_a_fanned_out_run_names_the_unit_as_well(self) -> None:
        metrics = {
            "anomalies_total": 1,
            "units": {"uut1": {"nvme0": {"verify_ok": False, "verify_failures": 9}}},
        }
        profile = TidSsdProfile(pass_criteria={"max_verify_failures": 0})
        ok, reason = _evaluate([IterationOutcome(success=True, metrics=metrics)], profile)
        assert not ok
        assert "uut1/nvme0" in reason

    def test_a_drive_off_the_bus_at_the_end_fails_only_when_asked(self) -> None:
        metrics = {"anomalies_total": 0, "nvme0": {"verify_ok": None, "device_present": False}}
        outcomes = [IterationOutcome(success=True, metrics=metrics)]
        assert _evaluate(outcomes, TidSsdProfile()) == (True, "")
        strict = TidSsdProfile(pass_criteria={"require_device_at_end": True})
        assert _evaluate(outcomes, strict)[0] is False

    def test_a_clean_session_passes(self) -> None:
        metrics = {"anomalies_total": 0, "nvme0": {"verify_ok": True, "verify_failures": 0, "device_present": True}}
        assert _evaluate([IterationOutcome(success=True, metrics=metrics)], TidSsdProfile()) == (True, "")


class TestJudgement:
    def test_a_healthy_tick_raises_nothing(self) -> None:
        assert kinds(DeviceState(), HEALTHY) == set()

    def test_a_drive_off_the_bus_is_reported_apart_from_the_io_failing(self) -> None:
        state = DeviceState()
        assert "device/missing" in kinds(state, {**HEALTHY, "device_present": False})
        assert state.missing_ticks == 1

    def test_a_test_path_on_another_disk_is_reported_because_the_bandwidth_would_be_that_disks(self) -> None:
        found = kinds(
            DeviceState(),
            {
                **HEALTHY,
                "test_path_ok": False,
                "test_path_disk": "mmcblk0",
                "write_mbps": None,
                "read_mbps": None,
                "verify_ok": None,
            },
        )
        assert "device/test_path_not_on_device" in found

    def test_a_failed_cache_drop_is_reported_because_the_read_rate_is_then_fiction(self) -> None:
        assert "ssh/cache_drop_failed" in kinds(DeviceState(), {**HEALTHY, "cache_drop_failed": True})

    def test_a_miscompare_is_counted_and_raised(self) -> None:
        state = DeviceState()
        result = {**HEALTHY, "verify_ok": False, "verify_actual_sha": "b" * 64}
        assert "write_verify/miscompare" in kinds(state, result)
        assert state.verify_failures == 1

    def test_a_rate_under_the_floor_is_raised_and_one_over_it_is_not(self) -> None:
        block = ProbeBlock(read_floor_mbps=500.0, write_floor_mbps=300.0)
        assert kinds(DeviceState(), HEALTHY, block) == set()
        slow = {**HEALTHY, "read_mbps": 100.0, "write_mbps": 100.0}
        assert kinds(DeviceState(), slow, block) == {
            "bandwidth/read_below_floor",
            "bandwidth/write_below_floor",
        }

    def test_counters_are_judged_against_the_session_baseline(self) -> None:
        state = DeviceState(smart_baseline={"media_errors": 5})
        unchanged = {**HEALTHY, "smart": {"media_errors": 5}}
        assert kinds(state, unchanged) == set()
        assert "smart/media_error_increased" in kinds(state, {**HEALTHY, "smart": {"media_errors": 6}})

    def test_a_counter_that_rose_once_does_not_raise_again_until_it_moves(self) -> None:
        state = DeviceState()
        moved = {**HEALTHY, "smart": {"media_errors": 3}}
        assert "smart/media_error_increased" in kinds(state, moved)
        assert kinds(state, moved) == set()

    def test_a_draining_spare_pool_is_raised_once_per_step(self) -> None:
        state = DeviceState()
        kinds(state, HEALTHY)
        falling = {**HEALTHY, "smart": {"available_spare": 90, "available_spare_threshold": 10}}
        assert "smart/spare_depleting" in kinds(state, falling)
        assert kinds(state, falling) == set()

    def test_falling_under_the_drives_own_threshold_is_raised_once(self) -> None:
        state = DeviceState()
        kinds(state, HEALTHY)
        empty = {**HEALTHY, "smart": {"available_spare": 5, "available_spare_threshold": 10}}
        assert "smart/spare_below_threshold" in kinds(state, empty)
        assert "smart/spare_below_threshold" not in kinds(state, empty)


class TestMockDisk:
    def test_a_conformance_length_run_stays_healthy(self) -> None:
        state = DeviceState()
        for iteration in range(12):
            assert kinds(state, mock.probe(iteration, DEVICE)) == set()

    def test_the_symptoms_arrive_in_the_order_a_real_part_shows_them(self) -> None:
        state = DeviceState()
        first_seen: dict[str, int] = {}
        for iteration in range(mock.DROPOUT_ONSET + 5):
            for kind in kinds(state, mock.probe(iteration, DEVICE), ProbeBlock(read_floor_mbps=2900.0)):
                first_seen.setdefault(kind, iteration)
        assert first_seen["bandwidth/read_below_floor"] < first_seen["smart/media_error_increased"]
        assert first_seen["smart/media_error_increased"] < first_seen["smart/spare_depleting"]
        assert first_seen["smart/spare_depleting"] < first_seen["write_verify/miscompare"]
        assert first_seen["write_verify/miscompare"] < first_seen["device/missing"]

    def test_the_same_tick_synthesises_the_same_disk(self) -> None:
        assert mock.probe(50, DEVICE) == mock.probe(50, DEVICE)

    def test_each_unit_gets_its_own_numbers_on_the_same_schedule(self) -> None:
        first = mock.probe(50, DEVICE, "uut0")
        second = mock.probe(50, DEVICE, "uut1")
        assert first["write_mbps"] != second["write_mbps"]
        assert first["smart"]["media_errors"] == second["smart"]["media_errors"]


class TestKernelLog:
    def test_only_lines_since_the_cursor_and_matching_a_pattern_are_returned(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import suite.probe as probe_module

        ring = "boot ok\nnvme nvme0: pci function\nunrelated chatter\nblk_update_request: I/O error"

        class Result:
            stdout = ring

        monkeypatch.setattr(probe_module, "run", lambda *a, **k: Result())
        lines, cursor = read_kernel_log(object(), DmesgBlock(), since_cursor="nvme nvme0: pci function", timeout=5.0)
        assert lines == ["blk_update_request: I/O error"]
        assert cursor == "blk_update_request: I/O error"

    def test_an_empty_ring_leaves_the_cursor_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import suite.probe as probe_module

        class Result:
            stdout = ""

        monkeypatch.setattr(probe_module, "run", lambda *a, **k: Result())
        assert read_kernel_log(object(), DmesgBlock(), since_cursor="earlier", timeout=5.0) == ([], "earlier")
