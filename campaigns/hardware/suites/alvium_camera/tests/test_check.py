"""What makes a still good enough to say the camera is working."""

from __future__ import annotations

import pytest
from gauntlet_sdk import IterationOutcome
from pydantic import ValidationError
from suite.camera import Snapshot, Temperature
from suite.profile import CameraCheckProfile
from suite.runner import _evaluate, _fault, _is_alvium

COOL = Temperature(mainboard_c=30.0, sensor_c=32.0, status="OK")


def still(*, mean_luma: float = 120.0, sharpness: float = 3.0) -> Snapshot:
    return Snapshot(
        height=48,
        image=b"png",
        mean_luma=mean_luma,
        sharpness=sharpness,
        suffix=".png",
        width=64,
    )


def outcome(success: bool, reason: str = "") -> IterationOutcome:
    return IterationOutcome(success=success, reason=reason, metrics={}, summary="")


class TestProfile:
    def test_an_unknown_driver_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CameraCheckProfile(driver="usb")

    def test_a_real_camera_cannot_be_asked_for_faster_than_it_reads(self) -> None:
        with pytest.raises(ValidationError):
            CameraCheckProfile(driver="real", sample_period_s=0.1)

    def test_a_brightness_window_with_nothing_inside_it_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CameraCheckProfile(min_mean_luma=200, max_mean_luma=100)


class TestWhatAnsweredTheCapability:
    def test_an_allied_vision_camera_is_what_the_run_wants(self) -> None:
        _is_alvium({"driver": "alvium", "serial": "0GL7P"})

    def test_a_capture_node_is_refused_by_name(self) -> None:
        # `camera_device: auto` hands over a webcam when no Allied Vision
        # camera was on the bus at scan time, and the run would otherwise
        # measure it and report it as the camera under test.
        with pytest.raises(RuntimeError, match="not an Allied Vision camera"):
            _is_alvium({"driver": "uvc", "node": "/dev/video0"})

    def test_a_driver_that_says_nothing_is_refused_too(self) -> None:
        with pytest.raises(RuntimeError, match="another driver"):
            _is_alvium({"node": "/dev/video0"})


class TestExposureSetting:
    def test_it_is_left_to_the_camera_by_default(self) -> None:
        assert CameraCheckProfile().exposure_us == 0

    def test_a_negative_exposure_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            CameraCheckProfile(exposure_us=-1)


class TestFault:
    def test_a_lit_scene_is_fine(self) -> None:
        assert _fault(still(), COOL, 0, CameraCheckProfile()) == ""

    def test_a_dark_frame_is_no_picture(self) -> None:
        assert "dark" in _fault(still(mean_luma=1.0), COOL, 0, CameraCheckProfile())

    def test_a_blown_out_frame_is_no_picture_either(self) -> None:
        assert "saturated" in _fault(still(mean_luma=254.0), COOL, 0, CameraCheckProfile())

    def test_a_frame_with_no_edges_is_a_lens_cap(self) -> None:
        assert "no detail" in _fault(still(sharpness=0.0), COOL, 0, CameraCheckProfile())

    def test_a_repeated_frame_is_a_locked_up_image_path(self) -> None:
        assert "repeating" in _fault(still(), COOL, 2, CameraCheckProfile(max_identical_frames=1))

    def test_a_hot_sensor_fails_before_the_firmware_shuts_it_down(self) -> None:
        hot = Temperature(mainboard_c=80.0, sensor_c=90.0, status="OK")
        assert "sensor is at" in _fault(still(), hot, 0, CameraCheckProfile())

    def test_a_camera_at_seventy_is_working_and_not_a_fault(self) -> None:
        # What an unmounted 1800 U reads while producing usable frames, so a
        # ceiling below it would fail every bench run.
        warm = Temperature(mainboard_c=67.5, sensor_c=70.2, status="OK")
        assert _fault(still(), warm, 0, CameraCheckProfile()) == ""

    def test_the_cameras_own_complaint_is_taken_over_the_number(self) -> None:
        unhappy = Temperature(mainboard_c=60.0, sensor_c=64.0, status="Critical")
        assert "reports its temperature as Critical" in _fault(still(), unhappy, 0, CameraCheckProfile())

    def test_a_camera_that_would_not_report_its_temperature_is_not_a_fault(self) -> None:
        # Zero is what an unreadable temperature comes back as, and a check
        # that failed on it would fail every mock run.
        unknown = Temperature(mainboard_c=0.0, sensor_c=0.0, status="")
        assert _fault(still(), unknown, 0, CameraCheckProfile()) == ""


class TestVerdict:
    def test_a_run_with_no_stills_is_a_no(self) -> None:
        assert _evaluate([], CameraCheckProfile()) == (False, "no stills taken")

    def test_one_bad_still_out_of_five_is_a_no(self) -> None:
        outcomes = [outcome(True)] * 4 + [outcome(False, "frame is dark")]
        passed, reason = _evaluate(outcomes, CameraCheckProfile())
        assert passed is False
        assert "1 of 5" in reason

    def test_every_still_usable_is_a_yes(self) -> None:
        assert _evaluate([outcome(True)] * 5, CameraCheckProfile()) == (True, "")
