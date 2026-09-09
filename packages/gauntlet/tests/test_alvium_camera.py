"""The Allied Vision driver, against a stand-in for the transport layer.

Nothing here needs a camera attached, or the GenTL layer installed: every test
hands the driver a fake ``VmbSystem`` in place of the one ``vmbpy`` would give
it. The real exception types are used, because catching them is the part of the
driver most easily got wrong.
"""

from __future__ import annotations

import base64
import struct
from pathlib import Path
from typing import Any

import pytest
import vmbpy

from gauntlet.capabilities import CapabilityRegistry, CommandRejected
from gauntlet.config import Settings
from gauntlet.instruments import detect_instruments, is_simulated
from gauntlet.instruments.alvium_camera import AlviumCamera
from gauntlet.instruments.imaging import ImageError, scale_rgb

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

_SERIAL = "0GL7P"
# Stands in for the usbfs node. A real one is a bus address that moves whenever
# the camera is replugged, and the driver only ever asks whether it can be
# opened, so the one node every host has serves.
_NODE = Path("/dev/null")

# What a camera in a SuperSpeed port negotiates, which is what every test
# camera is on unless it is testing what happens when it is not.
_SUPERSPEED = 5000


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def rgb_frame(width: int, height: int, red: int, green: int, blue: int) -> bytes:
    """A flat RGB8 frame of one colour."""
    return bytes((red, green, blue)) * width * height


def png_size(payload: bytes) -> tuple[int, int]:
    """The width and height an encoded PNG declares in its IHDR."""
    assert payload.startswith(_PNG_SIGNATURE)
    width, height = struct.unpack(">II", payload[16:24])
    return width, height


class _Feature:
    """One camera feature, with whatever facets the driver asks it for."""

    def __init__(self, value: Any, *, entries: tuple[str, ...] = (), span: tuple[float, float] | None = None) -> None:
        self.value = value
        self.error: Exception | None = None
        self._entries = entries
        self._span = span

    def get(self) -> Any:
        if self.error is not None:
            raise self.error
        return self.value

    def set(self, value: Any) -> None:
        if self.error is not None:
            raise self.error
        self.value = value

    def get_range(self) -> tuple[float, float]:
        assert self._span is not None
        return self._span

    def get_available_entries(self) -> tuple[str, ...]:
        return self._entries


class _Command:
    """One camera command feature."""

    def __init__(self) -> None:
        self.ran = 0
        self.error: Exception | None = None

    def run(self) -> None:
        if self.error is not None:
            raise self.error
        self.ran += 1


class _Temperature:
    """The reading, which answers for whichever sensor the selector names."""

    def __init__(self, selector: _Feature, readings: dict[str, float]) -> None:
        self._selector = selector
        self._readings = readings
        self.error: Exception | None = None

    def get(self) -> float:
        if self.error is not None:
            raise self.error
        return self._readings[str(self._selector.value)]


class _Frame:
    """One captured frame."""

    def __init__(self, width: int, height: int, data: bytes, status: Any = vmbpy.FrameStatus.Complete) -> None:
        self._data = data
        self._height = height
        self._status = status
        self._width = width

    def get_buffer(self) -> bytes:
        return self._data

    def get_height(self) -> int:
        return self._height

    def get_status(self) -> Any:
        return self._status

    def get_width(self) -> int:
        return self._width


class _FakeCamera:
    """Enough of a ``vmbpy`` camera to answer the driver."""

    def __init__(self, serial: str = _SERIAL, *, width: int = 64, height: int = 48) -> None:
        self.entered = False
        self.exited = False
        self.enter_error: Exception | None = None
        self.frame_error: Exception | None = None
        self.frames_grabbed = 0
        self._serial = serial
        selector = _Feature("Sensor", entries=("Sensor", "Mainboard"))
        self.features: dict[str, Any] = {
            "DeviceFirmwareVersion": _Feature("14.3.4646D35D"),
            "DeviceTemperature": _Temperature(selector, {"Sensor": 30.6, "Mainboard": 32.7}),
            "DeviceTemperatureSelector": selector,
            "DeviceTemperatureStatus": _Feature("OK"),
            "ExposureAuto": _Feature("Off", entries=("Off", "Once", "Continuous")),
            "ExposureTime": _Feature(5005.5, span=(70.0, 9_999_977.0)),
            "GainAuto": _Feature("Off", entries=("Off", "Once", "Continuous")),
            "Height": _Feature(height),
            "PayloadSize": _Feature(width * height * 3),
            "DeviceReset": _Command(),
            "Width": _Feature(width),
        }
        self.frame = _Frame(width, height, rgb_frame(width, height, 200, 100, 50))
        self.pixel_format = vmbpy.PixelFormat.BayerRG8
        self.pixel_format_error: Exception | None = None

    def get_serial(self) -> str:
        return self._serial

    def get_model(self) -> str:
        return "1800 U-2040c"

    def get_pixel_format(self) -> Any:
        return self.pixel_format

    def set_pixel_format(self, value: Any) -> None:
        if self.pixel_format_error is not None:
            raise self.pixel_format_error
        self.pixel_format = value

    def get_feature_by_name(self, name: str) -> Any:
        return self.features[name]

    def get_frame(self, timeout_ms: int = 0) -> _Frame:
        if self.frame_error is not None:
            raise self.frame_error
        self.frames_grabbed += 1
        return self.frame

    def __enter__(self) -> _FakeCamera:
        if self.enter_error is not None:
            raise self.enter_error
        self.entered = True
        return self

    def __exit__(self, *args: Any) -> None:
        self.exited = True


class _FakeSystem:
    """Enough of ``VmbSystem`` to stand in for the transport layer."""

    def __init__(self, *cameras: _FakeCamera, enter_error: Exception | None = None) -> None:
        self.cameras = list(cameras)
        self.enter_error = enter_error
        self.entered = 0
        self.exited = 0

    def __enter__(self) -> _FakeSystem:
        if self.enter_error is not None:
            raise self.enter_error
        self.entered += 1
        return self

    def __exit__(self, *args: Any) -> None:
        self.exited += 1

    def get_all_cameras(self) -> tuple[_FakeCamera, ...]:
        return tuple(self.cameras)


def camera_with(
    system: _FakeSystem,
    *,
    present: list[tuple[str, Path, int]] | None = None,
    **kwargs: Any,
) -> AlviumCamera:
    """A driver wired to one stand-in transport layer."""
    candidates = [(_SERIAL, _NODE, _SUPERSPEED)] if present is None else present
    return AlviumCamera(presence=lambda: candidates, system=lambda: system, **kwargs)


class TestScaleRgb:
    def test_a_frame_within_the_limit_is_kept_whole(self) -> None:
        pixels, width, height = scale_rgb(rgb_frame(8, 4, 1, 2, 3), 8, 4, max_width=64)
        assert (width, height) == (8, 4)
        assert bytes(pixels[:3]) == b"\x01\x02\x03"

    def test_no_limit_keeps_every_pixel(self) -> None:
        _, width, height = scale_rgb(rgb_frame(8, 4, 1, 2, 3), 8, 4)
        assert (width, height) == (8, 4)

    def test_a_frame_is_subsampled_to_land_inside_the_limit(self) -> None:
        pixels, width, height = scale_rgb(rgb_frame(64, 32, 9, 8, 7), 64, 32, max_width=16)
        assert (width, height) == (16, 8)
        assert len(pixels) == 16 * 8 * 3
        assert bytes(pixels[:3]) == b"\x09\x08\x07"

    def test_a_short_frame_is_refused(self) -> None:
        with pytest.raises(ImageError, match="bytes"):
            scale_rgb(b"\x00" * 10, 64, 32)


class TestAvailability:
    def test_a_camera_on_the_bus_is_available_without_opening_one(self) -> None:
        system = _FakeSystem(_FakeCamera())
        camera = camera_with(system)
        assert camera.available() is True
        assert system.entered == 0

    def test_an_empty_bus_is_unavailable(self) -> None:
        camera = camera_with(_FakeSystem(), present=[])
        assert camera.available() is False
        assert "no Allied Vision camera" in camera.describe()["unavailable_reason"]

    def test_a_named_serial_that_is_absent_says_so(self) -> None:
        camera = camera_with(_FakeSystem(), present=[("OTHER", _NODE, _SUPERSPEED)], serial_filter=_SERIAL)
        assert camera.available() is False
        assert "0GL7P: not present" in camera.describe()["unavailable_reason"]

    def test_a_node_that_cannot_be_opened_names_the_udev_rules(self, tmp_path: Path) -> None:
        node = tmp_path / "unreadable"
        node.write_bytes(b"")
        node.chmod(0o000)
        camera = camera_with(_FakeSystem(), present=[(_SERIAL, node, _SUPERSPEED)])
        assert camera.available() is False
        assert "install the udev rules" in camera.describe()["unavailable_reason"]


class TestOwning:
    def test_the_key_answers_at_once_after_the_camera_was_released(self) -> None:
        # The probe interval holds off failures so the panel's poll does not
        # start a transport layer several times a second. A camera that just
        # opened is not one of those, and an operator toggling the key would
        # otherwise be refused with no reason to show.
        clock = _Clock()
        camera = camera_with(_FakeSystem(_FakeCamera()), clock=clock)
        assert camera.own() is True
        camera.disown()
        assert camera.own() is True
        assert camera.describe()["unavailable_reason"] == ""

    def test_a_released_camera_says_it_is_not_owned_rather_than_nothing(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        camera.disown()
        assert camera.describe()["unavailable_reason"] == "not owned"

    def test_owning_opens_the_camera_and_settles_the_format(self) -> None:
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake))
        assert camera.owned() is False
        assert camera.own() is True
        assert fake.entered is True
        assert fake.pixel_format is vmbpy.PixelFormat.Rgb8
        assert camera.owned() is True
        assert camera.describe()["unavailable_reason"] == ""
        assert camera.describe()["resolution"] == "64x48 Rgb8"
        assert camera.describe()["firmware"] == "14.3.4646D35D"
        assert camera.describe()["serial"] == _SERIAL

    def test_a_transport_layer_that_will_not_start_is_reported(self) -> None:
        system = _FakeSystem(_FakeCamera(), enter_error=vmbpy.VmbTransportLayerError("no TL"))
        camera = camera_with(system)
        assert camera.own() is False
        assert "transport layer did not load" in camera.describe()["unavailable_reason"]

    def test_a_camera_that_will_not_open_releases_the_transport_layer(self) -> None:
        fake = _FakeCamera()
        fake.enter_error = vmbpy.VmbCameraError("camera in use")
        system = _FakeSystem(fake)
        camera = camera_with(system)
        assert camera.own() is False
        assert "camera in use" in camera.describe()["unavailable_reason"]
        assert system.exited == 1

    def test_a_camera_whose_image_path_is_shut_down_names_the_temperature(self) -> None:
        """What an over-temperature camera looks like: it opens, and then will
        not say how wide it is. The access error alone says nothing about why,
        so the reading that does is put beside it."""
        fake = _FakeCamera()
        fake.pixel_format_error = vmbpy.VmbFeatureError("Invalid access")
        fake.features["DeviceTemperature"]._readings["Sensor"] = 84.7
        fake.features["DeviceTemperatureStatus"].value = "OverTemperature"
        camera = camera_with(_FakeSystem(fake))
        assert camera.own() is False
        reason = camera.describe()["unavailable_reason"]
        assert "Invalid access" in reason
        assert "sensor 84.7C" in reason
        assert "OverTemperature" in reason

    def test_a_camera_that_cannot_report_its_temperature_either_is_left_as_it_is(self) -> None:
        fake = _FakeCamera()
        fake.pixel_format_error = vmbpy.VmbFeatureError("Invalid access")
        fake.features["DeviceTemperature"].error = vmbpy.VmbFeatureError("gone")
        camera = camera_with(_FakeSystem(fake))
        assert camera.own() is False
        assert camera.describe()["unavailable_reason"].endswith("Invalid access")

    def test_a_failed_own_is_not_retried_until_the_interval_passes(self) -> None:
        clock = _Clock()
        system = _FakeSystem(enter_error=vmbpy.VmbTransportLayerError("no TL"))
        camera = camera_with(system, clock=clock, probe_interval_s=3.0)
        assert camera.own() is False
        assert camera.own() is False
        assert system.entered == 0

        clock.advance(3.0)
        assert camera.own() is False

    def test_only_the_named_serial_is_taken(self) -> None:
        wanted = _FakeCamera("WANTED")
        other = _FakeCamera("OTHER")
        camera = camera_with(
            _FakeSystem(other, wanted),
            present=[("WANTED", _NODE, _SUPERSPEED)],
            serial_filter="WANTED",
        )
        assert camera.own() is True
        assert wanted.entered is True
        assert other.entered is False

    def test_disowning_releases_both_the_camera_and_the_layer(self) -> None:
        fake = _FakeCamera()
        system = _FakeSystem(fake)
        camera = camera_with(system)
        camera.own()
        camera.disown()
        assert fake.exited is True
        assert system.exited == 1
        assert camera.owned() is False

    def test_owning_an_owned_camera_does_not_open_it_again(self) -> None:
        system = _FakeSystem(_FakeCamera())
        camera = camera_with(system)
        assert camera.own() is True
        assert camera.own() is True
        assert system.entered == 1


class TestExposure:
    def test_pinning_it_reads_back_what_the_camera_settled_on(self) -> None:
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        assert camera.command("set_exposure", {"exposure_us": 45_000}) == {"exposure_us": 45_000.0}
        assert camera.state()["format"]["exposure_us"] == 45_000.0

    def test_pinning_it_takes_both_kinds_of_metering_off_the_camera(self) -> None:
        # Gain left on auto compensates for a sensor that is dimming just as
        # exposure does, which is what a dose run is there to record.
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        camera.command("set_exposure", {"exposure_us": 45_000})
        assert fake.features["ExposureAuto"].value == "Off"
        assert fake.features["GainAuto"].value == "Off"
        assert camera.state()["format"]["exposure_auto"] == "Off"

    def test_a_pinned_exposure_is_reported_through_the_state_a_write_answers_with(self) -> None:
        # A suite drives this through the capability, which answers anything
        # but a snapshot with the state rather than the command's own reply.
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        answered = camera.write({"command": "set_exposure", "args": {"exposure_us": 45_000}})
        assert answered["format"]["exposure_us"] == 45_000.0

    def test_metering_can_be_handed_back_without_releasing_the_camera(self) -> None:
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        camera.command("set_exposure", {"exposure_us": 45_000})
        camera.command("set_auto_exposure", {})
        assert fake.features["ExposureAuto"].value == "Continuous"

    def test_a_value_outside_the_range_is_rejected(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        with pytest.raises(CommandRejected, match="between"):
            camera.command("set_exposure", {"exposure_us": 20_000_000})

    def test_a_value_that_is_not_a_number_is_rejected(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        with pytest.raises(CommandRejected, match="must be a number"):
            camera.command("set_exposure", {"exposure_us": "bright"})


class TestMetering:
    def test_the_camera_is_told_to_meter_itself(self) -> None:
        # It boots on a fixed 5ms and no gain, which is a black frame in any
        # room that is not brightly lit, and there is no exposure command for
        # an operator to correct it with.
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        assert fake.features["ExposureAuto"].value == "Continuous"
        assert fake.features["GainAuto"].value == "Continuous"

    def test_a_reopened_camera_meters_for_itself_again(self) -> None:
        # `reset` restores the boot defaults and so does a fresh connect, so a
        # run that pinned an exposure has to pin it again.
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        camera.command("set_exposure", {"exposure_us": 45_000})
        camera.disown()
        camera.own()
        assert camera.state()["format"]["exposure_auto"] == "Continuous"

    def test_a_camera_that_cannot_meter_itself_still_opens(self) -> None:
        fake = _FakeCamera()
        fake.features["ExposureAuto"].error = vmbpy.VmbFeatureError("no such feature")
        camera = camera_with(_FakeSystem(fake))
        assert camera.own() is True


class TestCommands:
    def test_the_same_commands_are_offered_owned_or_not(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        offered = ["set_owned", "reset", "set_exposure", "set_auto_exposure", "snapshot"]
        assert [row["name"] for row in camera.commands()] == offered
        camera.own()
        assert [row["name"] for row in camera.commands()] == offered

    def test_the_two_exposure_keys_share_one_dial(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        rows = {row["name"]: row for row in camera.commands()}
        assert rows["set_exposure"]["group"] == rows["set_auto_exposure"]["group"]
        assert rows["set_auto_exposure"]["fields"] == []

    def test_the_exposure_dial_is_the_range_the_camera_reports(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        field = next(row for row in camera.commands() if row["name"] == "set_exposure")["fields"][0]
        assert (field["min"], field["max"]) == (70.0, 9_999_977.0)

    def test_the_reboot_sits_with_the_viewer_rather_than_in_the_deck(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        reset = next(row for row in camera.commands() if row["name"] == "reset")
        assert reset["role"] == "viewer"

    def test_the_key_opens_and_releases_the_camera(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        assert camera.command("set_owned", {"owned": True}) == {"owned": True}
        assert camera.owned() is True
        camera.command("set_owned", {"owned": False})
        assert camera.owned() is False

    def test_the_key_rejects_a_value_that_is_not_a_flag(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        with pytest.raises(CommandRejected, match="true or false"):
            camera.command("set_owned", {"owned": "yes"})

    def test_an_unavailable_camera_cannot_be_owned_by_the_key(self) -> None:
        camera = camera_with(_FakeSystem(), present=[])
        with pytest.raises(CommandRejected, match="unavailable"):
            camera.command("set_owned", {"owned": True})

    def test_a_command_before_owning_is_refused(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        with pytest.raises(CommandRejected, match="not owned"):
            camera.command("snapshot", {})

    def test_an_unknown_command_is_refused(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        with pytest.raises(CommandRejected, match="no command"):
            camera.command("record", {})


class TestReset:
    def test_it_reboots_the_camera_and_releases_it(self) -> None:
        fake = _FakeCamera()
        system = _FakeSystem(fake)
        camera = camera_with(system)
        camera.own()
        assert camera.command("reset", {}) == {"reset": True}
        assert fake.features["DeviceReset"].ran == 1
        # It leaves the bus, so holding it open would hold a camera that is
        # no longer there.
        assert camera.owned() is False
        assert system.exited == 1

    def test_a_shut_down_camera_can_still_be_rebooted(self) -> None:
        """The whole point: one the firmware has shut down refuses to be
        owned, so requiring ownership would put the recovery out of reach."""
        fake = _FakeCamera()
        fake.pixel_format_error = vmbpy.VmbFeatureError("Invalid access")
        system = _FakeSystem(fake)
        camera = camera_with(system)
        assert camera.own() is False
        assert camera.command("reset", {}) == {"reset": True}
        assert fake.features["DeviceReset"].ran == 1
        # Opened for the reboot and released again, so nothing is left holding
        # a camera that has gone off the bus.
        assert camera.owned() is False
        assert system.exited == 2

    def test_rebooting_with_nothing_on_the_bus_is_refused(self) -> None:
        camera = camera_with(_FakeSystem(), present=[])
        with pytest.raises(CommandRejected, match="unavailable"):
            camera.command("reset", {})

    def test_a_camera_that_refuses_is_released_anyway(self) -> None:
        fake = _FakeCamera()
        fake.features["DeviceReset"].error = vmbpy.VmbFeatureError("gone")
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        with pytest.raises(CommandRejected, match="gone"):
            camera.command("reset", {})
        assert camera.owned() is False


class TestSnapshot:
    def test_it_writes_a_png_of_the_whole_frame(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        result = camera.command("snapshot", {})
        assert result["suffix"] == ".png"
        assert png_size(base64.b64decode(result["image_base64"])) == (64, 48)
        assert result["source"] == {"width": 64, "height": 48, "pixel_format": "Rgb8"}

    def test_a_width_caps_the_picture(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera(width=128, height=64)))
        camera.own()
        result = camera.command("snapshot", {"max_width": "32"})
        assert png_size(base64.b64decode(result["image_base64"])) == (32, 16)

    def test_the_widest_choice_is_no_cap_at_all(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera(width=128, height=64)))
        camera.own()
        result = camera.command("snapshot", {"max_width": "Full"})
        assert png_size(base64.b64decode(result["image_base64"])) == (128, 64)

    def test_a_live_snapshot_is_a_jpeg(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        result = camera.command("snapshot", {"live": True})
        assert result["suffix"] == ".jpg"
        assert base64.b64decode(result["image_base64"]).startswith(b"\xff\xd8")

    def test_what_was_in_the_frame_is_measured(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        result = camera.command("snapshot", {})
        assert result["mean_luma"] > 0
        # A flat frame has no edges in it at all.
        assert result["sharpness"] == 0.0
        assert camera.state()["snapshots"] == 1

    def test_a_width_wider_than_the_frame_is_the_whole_frame(self) -> None:
        # The panel offers a fixed list of presets and cannot know how wide
        # this camera is, so the widest of them has to mean "all of it".
        camera = camera_with(_FakeSystem(_FakeCamera(width=128, height=64)))
        camera.own()
        result = camera.command("snapshot", {"max_width": 9000})
        assert png_size(base64.b64decode(result["image_base64"])) == (128, 64)

    def test_a_width_below_what_is_worth_encoding_is_rejected(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        with pytest.raises(CommandRejected, match="at least 16"):
            camera.command("snapshot", {"max_width": 4})

    def test_a_width_that_is_not_a_number_is_rejected(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        with pytest.raises(CommandRejected, match="must be a number"):
            camera.command("snapshot", {"max_width": "wide"})

    def test_an_incomplete_frame_is_refused(self) -> None:
        fake = _FakeCamera()
        fake.frame = _Frame(64, 48, b"", status=vmbpy.FrameStatus.Incomplete)
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        with pytest.raises(CommandRejected, match="incomplete"):
            camera.command("snapshot", {})

    def test_an_incomplete_frame_on_a_usb_2_link_says_so(self) -> None:
        fake = _FakeCamera()
        fake.frame = _Frame(64, 48, b"", status=vmbpy.FrameStatus.Incomplete)
        camera = camera_with(_FakeSystem(fake), present=[(_SERIAL, _NODE, 480)])
        camera.own()
        with pytest.raises(CommandRejected, match="480 Mb/s link"):
            camera.command("snapshot", {})

    def test_a_camera_that_stops_answering_is_dropped(self) -> None:
        fake = _FakeCamera()
        fake.frame_error = vmbpy.VmbTimeout("no frame")
        camera = camera_with(_FakeSystem(fake))
        camera.own()
        with pytest.raises(CommandRejected, match="no frame"):
            camera.command("snapshot", {})
        assert camera.owned() is False


class TestState:
    def test_an_unowned_camera_has_nothing_to_report(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        state = camera.state()
        assert state["streaming"] is False
        assert state["format"] == {}
        assert state["temperature"] == {}

    def test_both_sensors_and_the_thermal_status_are_read(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        assert camera.state()["temperature"] == {"sensor": 30.6, "mainboard": 32.7, "status": "OK"}

    def test_the_temperature_is_not_re_read_until_the_interval_passes(self) -> None:
        clock = _Clock()
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake), clock=clock, temperature_interval_s=5.0)
        camera.own()
        assert camera.state()["temperature"]["sensor"] == 30.6

        fake.features["DeviceTemperature"]._readings["Sensor"] = 84.0
        assert camera.state()["temperature"]["sensor"] == 30.6

        clock.advance(5.0)
        assert camera.state()["temperature"]["sensor"] == 84.0

    def test_a_camera_that_stops_answering_reports_it_as_the_status(self) -> None:
        clock = _Clock()
        fake = _FakeCamera()
        camera = camera_with(_FakeSystem(fake), clock=clock)
        camera.own()
        fake.features["DeviceTemperature"].error = vmbpy.VmbFeatureError("gone")
        clock.advance(60.0)
        assert "gone" in camera.state()["temperature"]["status"]

    def test_every_readout_names_a_path_that_state_carries(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        camera.command("snapshot", {})
        state = camera.state()
        for entry in camera.readouts():
            cursor: Any = state
            for part in entry["key"].split("."):
                assert part in cursor, f"{entry['key']} is not in state()"
                cursor = cursor[part]

    def test_a_write_of_a_snapshot_answers_with_the_picture(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        camera.own()
        assert "image_base64" in camera.write({"command": "snapshot", "args": {}})

    def test_a_write_of_anything_else_answers_with_the_state(self) -> None:
        camera = camera_with(_FakeSystem(_FakeCamera()))
        assert camera.write({"command": "set_owned", "args": {"owned": True}})["streaming"] is True

    def test_it_is_not_a_simulation(self) -> None:
        assert is_simulated(camera_with(_FakeSystem(_FakeCamera()))) is False


class TestDetection:
    def test_a_camera_on_the_bus_wins_over_a_capture_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on_the_bus(monkeypatch, [(_SERIAL, _NODE, _SUPERSPEED)])
        monkeypatch.setattr("gauntlet.instruments.detect.AlviumCamera", _present_alvium)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device="auto", psu_port="", daq_serial=""))
        provider = registry.provider("camera")
        assert provider is not None
        assert provider.describe()["driver"] == "alvium"

    def test_a_camera_on_the_bus_that_cannot_be_opened_stays_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Dropping it would leave an operator told to check a page with
        # nothing on it, instead of the reason the camera will not open.
        _on_the_bus(monkeypatch, [(_SERIAL, _NODE, _SUPERSPEED)])
        monkeypatch.setattr("gauntlet.instruments.detect.AlviumCamera", _absent_alvium)
        monkeypatch.setattr("gauntlet.instruments.detect.UvcCamera", _present_uvc)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device="auto", psu_port="", daq_serial=""))
        provider = registry.provider("camera")
        assert provider is not None
        assert provider.describe()["driver"] == "alvium"

    def test_nothing_on_the_bus_falls_back_to_the_capture_node(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on_the_bus(monkeypatch, [])
        monkeypatch.setattr("gauntlet.instruments.detect.UvcCamera", _present_uvc)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device="auto", psu_port="", daq_serial=""))
        provider = registry.provider("camera")
        assert provider is not None
        assert provider.describe()["driver"] == "uvc"

    def test_a_named_camera_stays_registered_when_it_does_not_answer(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The operator said there is one there, so its absence is a fault to
        # show rather than an instrument to hide.
        monkeypatch.setattr("gauntlet.instruments.detect.AlviumCamera", _absent_alvium)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device=_SERIAL, psu_port="", daq_serial=""))
        provider = registry.provider("camera")
        assert provider is not None
        assert provider.describe()["driver"] == "alvium"

    def test_a_serial_names_an_allied_vision_camera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        taken: list[str] = []

        def build(*, serial_filter: str = "", **kwargs: Any) -> Any:
            taken.append(serial_filter)
            return _present_alvium()

        monkeypatch.setattr("gauntlet.instruments.detect.AlviumCamera", build)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device=_SERIAL, psu_port="", daq_serial=""))
        assert taken == [_SERIAL]
        assert registry.provider("camera") is not None

    def test_a_node_path_names_a_capture_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("gauntlet.instruments.detect.UvcCamera", _present_uvc)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device="/dev/video0", psu_port="", daq_serial=""))
        provider = registry.provider("camera")
        assert provider is not None
        assert provider.describe()["driver"] == "uvc"


def _on_the_bus(monkeypatch: pytest.MonkeyPatch, candidates: list[tuple[str, Path, int]]) -> None:
    """What sysfs is to say is plugged in, for the detection tests."""
    monkeypatch.setattr("gauntlet.instruments.detect.candidate_cameras", lambda: candidates)


def _stub(driver: str, *, present: bool = True) -> Any:
    """A provider that does nothing, for the detection tests."""

    class _Stub:
        name = "camera"

        def available(self) -> bool:
            return present

        def close(self) -> None:
            return None

        def describe(self) -> dict[str, str]:
            return {"driver": driver}

        def instance_id(self) -> str:
            return "camera0"

    return _Stub()


def _absent_alvium(**kwargs: Any) -> Any:
    return _stub("alvium", present=False)


def _present_alvium(**kwargs: Any) -> Any:
    return _stub("alvium")


def _present_uvc(**kwargs: Any) -> Any:
    return _stub("uvc")
