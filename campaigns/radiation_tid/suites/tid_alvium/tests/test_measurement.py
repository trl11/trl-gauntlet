"""What a still is judged against, and what the mock camera does."""

from __future__ import annotations

import pytest
from gauntlet_sdk import IterationOutcome
from pydantic import ValidationError
from suite import mock
from suite.camera import CameraError
from suite.mock import MockCamera
from suite.profile import CameraDoseProfile, PassCriteria
from suite.runner import _evaluate


def outcome(success: bool) -> IterationOutcome:
    return IterationOutcome(success=success, reason="", metrics={}, summary="")


class TestProfile:
    def test_an_unknown_driver_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CameraDoseProfile(driver="usb")

    def test_a_real_camera_cannot_be_asked_for_faster_than_it_reads(self) -> None:
        with pytest.raises(ValidationError):
            CameraDoseProfile(driver="real", sample_period_s=0.1)

    def test_a_mock_camera_may_go_as_fast_as_it_likes(self) -> None:
        assert CameraDoseProfile(driver="mock", sample_period_s=0.1).sample_period_s == 0.1

    def test_a_duration_of_zero_is_a_session_the_operator_ends(self) -> None:
        assert CameraDoseProfile(duration_s=0).duration_s == 0


class TestVerdict:
    def test_a_session_that_took_no_still_is_not_a_pass(self) -> None:
        assert _evaluate([outcome(False)], CameraDoseProfile()) == (False, "no still arrived in the whole session")

    def test_a_session_that_measured_at_all_is_left_to_the_runner(self) -> None:
        # Degrading is what the run is watching for, so nothing here fails a
        # session that kept measuring through it.
        assert _evaluate([outcome(False), outcome(True)], CameraDoseProfile()) == (True, "")

    def test_the_requirement_can_be_turned_off(self) -> None:
        profile = CameraDoseProfile(pass_criteria=PassCriteria(require_measurement=False))
        assert _evaluate([outcome(False)], profile) == (True, "")


class TestMockCamera:
    def test_a_fresh_camera_hands_over_a_different_still_each_time(self) -> None:
        camera = MockCamera()
        first = camera.snapshot(max_width=160)
        second = camera.snapshot(max_width=160)
        assert first.image != second.image
        assert first.mean_luma > 0

    def test_it_stays_healthy_for_longer_than_a_conformance_run(self) -> None:
        camera = MockCamera()
        for _ in range(20):
            shot = camera.snapshot(max_width=160)
        assert shot.sharpness > 0
        assert camera.temperature().sensor_c < CameraDoseProfile().max_sensor_c

    def test_edge_detail_goes_before_brightness_does(self) -> None:
        camera = MockCamera()
        for _ in range(mock.DIM_ONSET - 1):
            shot = camera.snapshot(max_width=160)
        baseline = MockCamera().snapshot(max_width=160)
        assert shot.sharpness < baseline.sharpness

    def test_the_image_path_locks_up_before_the_camera_goes(self) -> None:
        camera = MockCamera()
        for _ in range(mock.FREEZE_ONSET + 1):
            first = camera.snapshot(max_width=160)
        assert camera.snapshot(max_width=160).image == first.image

    def test_a_shut_down_camera_refuses_every_still(self) -> None:
        camera = MockCamera()
        with pytest.raises(CameraError):
            for _ in range(mock.DROPOUT_ONSET + 1):
                camera.snapshot(max_width=160)

    def test_a_shut_down_camera_cannot_be_owned_either(self) -> None:
        camera = MockCamera()
        with pytest.raises(CameraError):
            for _ in range(mock.DROPOUT_ONSET + 1):
                camera.snapshot(max_width=160)
        with pytest.raises(CameraError):
            camera.own()

    def test_a_reboot_brings_it_back_for_a_while(self) -> None:
        camera = MockCamera()
        with pytest.raises(CameraError):
            for _ in range(mock.DROPOUT_ONSET + 1):
                camera.snapshot(max_width=160)
        camera.reset()
        camera.own()
        assert camera.snapshot(max_width=160).image
