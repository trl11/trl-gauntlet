"""The camera driver, its simulation, and the image encoder behind both.

Every test here runs against a stand-in for the device, so none of them needs
a camera attached.
"""

from __future__ import annotations

import base64
import ctypes
import errno
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from gauntlet.capabilities import CapabilityRegistry, CommandRejected
from gauntlet.config import Settings
from gauntlet.instruments import MockCamera, detect_instruments, gmsl, imaging, is_simulated, v4l2
from gauntlet.instruments.imaging import ImageError, encode_frame, encode_png, image_suffix, measure, yuyv_to_rgb
from gauntlet.instruments.uvc_camera import UvcCamera
from gauntlet.instruments.v4l2 import (
    PIXELFORMAT_MJPG,
    PIXELFORMAT_YUYV,
    Frame,
    V4l2Error,
    V4l2Timeout,
    fourcc,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def yuyv_frame(width: int, height: int, luma: int = 128, chroma: int = 128) -> bytes:
    """A flat YUYV frame of one shade."""
    return bytes((luma, chroma, luma, chroma)) * (width // 2) * height


def png_size(payload: bytes) -> tuple[int, int]:
    """The width and height an encoded PNG declares in its IHDR."""
    assert payload.startswith(_PNG_SIGNATURE)
    width, height = struct.unpack(">II", payload[16:24])
    return width, height


class _FakeCamera:
    """Enough of a V4L2 device to answer the driver."""

    def __init__(self, path: Path) -> None:
        self.bursts: list[int] = []
        self.close_error: Exception | None = None
        self.closed = False
        self.frames_grabbed = 0
        self.grab_error: Exception | None = None
        self.open_error: Exception | None = None
        self.path = path
        self.pixelformat = PIXELFORMAT_YUYV
        self.started = False
        self.height = 8
        self.width = 8

    def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error

    def close(self) -> None:
        self.closed = True
        self.started = False
        if self.close_error is not None:
            raise self.close_error

    def describe(self) -> dict[str, str]:
        return {"bus_info": "usb-0000:07:00.1-4.4", "card": "LI-IMX728", "driver": "uvcvideo"}

    def format(self) -> dict[str, Any]:
        return {
            "bytesperline": self.width * 2,
            "fourcc": fourcc(self.pixelformat),
            "height": self.height,
            "pixelformat": self.pixelformat,
            "sizeimage": self.width * self.height * 2,
            "width": self.width,
        }

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def grab(self, *, timeout_s: float = 5.0) -> Frame:
        if self.grab_error is not None:
            raise self.grab_error
        self.frames_grabbed += 1
        # Each frame is a different shade, so a caller that keeps the wrong one
        # is visible in what it measured.
        return Frame(
            data=yuyv_frame(self.width, self.height, luma=16 * self.frames_grabbed),
            height=self.height,
            pixelformat=self.pixelformat,
            sequence=self.frames_grabbed,
            width=self.width,
        )

    def measure_stream(self, *, frames: int = 10, timeout_s: float = 5.0) -> dict[str, float]:
        if self.grab_error is not None:
            raise self.grab_error
        self.bursts.append(frames)
        return {"dropped": 0.0, "fps": 19.0, "frames": float(frames)}


@pytest.fixture(autouse=True)
def no_real_gmsl_link(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep a camera's link probe off whatever node this machine really has.

    `UvcCamera` probes for GMSL chips behind the node it opened, and every
    stand-in device is named `/dev/video0`, so without this a test would send
    extension-unit ioctls to real hardware. A test that wants a link patches
    `GmslLink` again.
    """
    monkeypatch.setattr("gauntlet.instruments.uvc_camera.GmslLink", lambda node: gmsl.GmslLink(tmp_path / "no-node"))


def camera_with(fake: _FakeCamera, *, present: bool = True, **kwargs: Any) -> UvcCamera:
    """A driver wired to one stand-in device, presumed present unless said otherwise."""
    return UvcCamera(
        device="/dev/video0",
        open_camera=lambda path: fake,
        presence=lambda device: present,
        **kwargs,
    )


class TestPngEncoding:
    def test_writes_a_readable_png_header(self) -> None:
        payload = encode_png(bytearray(b"\x00" * 3 * 4 * 2), 4, 2)
        assert payload.startswith(_PNG_SIGNATURE)
        assert png_size(payload) == (4, 2)

    def test_round_trips_the_pixels_through_zlib(self) -> None:
        pixels = bytearray(bytes(range(3)) * 4)
        payload = encode_png(pixels, 4, 1)
        # IHDR is 25 bytes after the signature, then the IDAT chunk's payload.
        start = len(_PNG_SIGNATURE) + 25
        length = struct.unpack(">I", payload[start : start + 4])[0]
        raw = zlib.decompress(payload[start + 8 : start + 8 + length])
        assert raw == b"\x00" + bytes(pixels)

    def test_pixels_that_do_not_fill_the_image_are_refused(self) -> None:
        with pytest.raises(ImageError, match="needs"):
            encode_png(bytearray(b"\x00" * 5), 4, 2)


class TestYuyvConversion:
    def test_neutral_chroma_is_grey(self) -> None:
        pixels, width, height = yuyv_to_rgb(yuyv_frame(4, 2, luma=100), 4, 2)
        assert (width, height) == (4, 2)
        assert set(pixels) == {100}

    def test_scaling_takes_every_nth_pixel(self) -> None:
        pixels, width, height = yuyv_to_rgb(yuyv_frame(8, 8), 8, 8, step=4)
        assert (width, height) == (2, 2)
        assert len(pixels) == 2 * 2 * 3

    def test_a_short_frame_is_refused(self) -> None:
        with pytest.raises(ImageError, match="YUYV frame is"):
            yuyv_to_rgb(b"\x00" * 4, 8, 8)

    def test_a_zero_step_is_refused(self) -> None:
        with pytest.raises(ImageError, match="at least 1"):
            yuyv_to_rgb(yuyv_frame(4, 2), 4, 2, step=0)


class TestMeasure:
    def test_a_flat_frame_has_no_sharpness(self) -> None:
        pixels, width, height = yuyv_to_rgb(yuyv_frame(8, 4, luma=60), 8, 4)
        measured = measure(pixels, width, height)
        assert measured["sharpness"] == 0.0
        assert measured["mean_luma"] == pytest.approx(60, abs=1)

    def test_a_dark_frame_reads_dark(self) -> None:
        pixels, width, height = yuyv_to_rgb(yuyv_frame(8, 4, luma=0), 8, 4)
        assert measure(pixels, width, height)["mean_luma"] == 0.0

    def test_alternating_bars_have_sharpness(self) -> None:
        # Two bright pixels then two dark, because the measurement reads every
        # other column and a one-pixel bar would fall between its samples.
        row = (bytes((235, 128, 235, 128)) + bytes((16, 128, 16, 128))) * 2
        pixels, width, height = yuyv_to_rgb(row * 4, 8, 4)
        assert measure(pixels, width, height)["sharpness"] > 0


class TestEncodeFrame:
    def test_yuyv_becomes_a_measured_png(self) -> None:
        frame = Frame(yuyv_frame(8, 8), PIXELFORMAT_YUYV, 8, 8, sequence=3)
        payload, measured = encode_frame(frame, max_width=4)
        assert payload.startswith(_PNG_SIGNATURE)
        assert png_size(payload) == (4, 4)
        assert measured["scale"] == 2
        assert "mean_luma" in measured

    def test_mjpeg_is_passed_through_untouched(self) -> None:
        frame = Frame(b"\xff\xd8\xff\xe0 not really a jpeg", PIXELFORMAT_MJPG, 8, 8, sequence=1)
        payload, measured = encode_frame(frame)
        assert payload == frame.data
        assert measured == {"width": 8, "height": 8}

    def test_an_unknown_format_is_refused(self) -> None:
        with pytest.raises(ImageError, match="cannot write"):
            encode_frame(Frame(b"\x00" * 8, 0x32315659, 2, 2, sequence=1))

    def test_suffix_follows_the_format(self) -> None:
        assert image_suffix(PIXELFORMAT_YUYV) == ".png"
        assert image_suffix(PIXELFORMAT_MJPG) == ".jpg"


class TestUvcCamera:
    def test_available_reports_presence_without_opening(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake)
        assert camera.available() is True
        assert fake.started is False

    def test_unavailable_when_no_candidate_is_present(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), present=False)
        assert camera.available() is False
        assert "not present" in camera.describe()["unavailable_reason"]

    def test_owning_opens_and_starts_the_stream(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake)
        assert camera.owned() is False
        assert camera.own() is True
        assert fake.started is True
        assert camera.owned() is True
        assert camera.describe()["unavailable_reason"] == ""

    def test_a_device_that_will_not_open_cannot_be_owned(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.open_error = V4l2Error("/dev/video0: device busy")
        camera = camera_with(fake)
        assert camera.own() is False
        assert camera.owned() is False
        assert "device busy" in camera.describe()["unavailable_reason"]

    def test_a_format_the_encoder_cannot_write_is_refused(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.pixelformat = 0x3231564E
        camera = camera_with(fake)
        assert camera.own() is False
        assert "not supported" in camera.describe()["unavailable_reason"]

    def test_a_failed_own_is_not_retried_until_the_interval_passes(self) -> None:
        clock = _Clock()
        fake = _FakeCamera(Path("/dev/video0"))
        fake.open_error = V4l2Error("/dev/video0: device has gone")
        opened: list[int] = []

        def build(path: Path) -> _FakeCamera:
            opened.append(1)
            return fake

        camera = UvcCamera(clock=clock, device="/dev/video0", open_camera=build, probe_interval_s=3.0)
        assert camera.own() is False
        assert camera.own() is False
        assert len(opened) == 1

        clock.advance(3.0)
        assert camera.own() is False
        assert len(opened) == 2

    def test_an_owned_device_is_not_reopened(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        opened: list[int] = []

        def build(path: Path) -> _FakeCamera:
            opened.append(1)
            return fake

        camera = UvcCamera(device="/dev/video0", open_camera=build)
        assert camera.own() is True
        assert camera.own() is True
        assert len(opened) == 1

    def test_snapshot_requires_ownership(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        with pytest.raises(CommandRejected, match="not owned"):
            camera.command("snapshot", {})

    def test_snapshot_returns_an_encoded_image(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), warmup_frames=0)
        camera.own()
        result = camera.command("snapshot", {})
        assert base64.b64decode(result["image_base64"]).startswith(_PNG_SIGNATURE)
        assert result["suffix"] == ".png"
        assert result["source"] == {"width": 8, "height": 8, "fourcc": "YUYV"}

    def test_snapshot_discards_the_frames_already_queued(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=2)
        camera.own()
        result = camera.command("snapshot", {})
        # Three grabbed, and the last is the one reported.
        assert fake.frames_grabbed == 3
        assert result["sequence"] == 3

    def test_state_counts_snapshots_without_taking_one(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=0)
        camera.own()
        camera.command("snapshot", {})
        grabbed = fake.frames_grabbed
        state = camera.state()
        assert state["snapshots"] == 1
        assert state["streaming"] is True
        assert fake.frames_grabbed == grabbed

    def test_a_camera_that_stops_answering_is_dropped(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=0)
        camera.own()
        fake.grab_error = V4l2Error("/dev/video0: device has gone")
        with pytest.raises(CommandRejected, match="device has gone"):
            camera.command("snapshot", {})
        assert fake.closed is True
        assert camera.owned() is False

    def test_a_late_frame_leaves_the_camera_owned(self) -> None:
        """A timeout is this capture missing, not the device going away.

        Dropping it would leave every later command in the run answering
        "not owned", which says nothing about the frame that did not arrive.
        """
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=0)
        camera.own()
        fake.grab_error = V4l2Timeout("/dev/video0: no frame within 5s")
        with pytest.raises(CommandRejected, match="no frame within 5s"):
            camera.command("snapshot", {})
        assert fake.closed is False
        assert camera.owned() is True

    def test_a_capture_after_a_late_frame_succeeds(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=0)
        camera.own()
        fake.grab_error = V4l2Timeout("/dev/video0: no frame within 5s")
        with pytest.raises(CommandRejected, match="no frame within 5s"):
            camera.command("snapshot", {})
        fake.grab_error = None
        assert camera.command("snapshot", {})["image_base64"]

    def test_an_unknown_command_is_rejected(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        camera.own()
        with pytest.raises(CommandRejected, match="no command"):
            camera.command("record", {})

    def test_a_width_out_of_range_is_rejected(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        camera.own()
        with pytest.raises(CommandRejected, match="between"):
            camera.command("snapshot", {"max_width": 4})

    def test_a_width_that_is_not_a_number_is_rejected(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        camera.own()
        with pytest.raises(CommandRejected, match="must be a number"):
            camera.command("snapshot", {"max_width": "wide"})

    def test_a_missing_width_takes_the_default(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), warmup_frames=0)
        camera.own()
        assert camera.command("snapshot", {})["width"] == 8

    def test_write_runs_a_command_and_returns_the_image(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), warmup_frames=0)
        camera.own()
        result = camera.write({"command": "snapshot", "args": {}})
        assert "image_base64" in result

    def test_close_releases_the_device(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake)
        camera.own()
        camera.close()
        assert fake.closed is True
        assert camera.owned() is False

    def test_set_owned_command_toggles_ownership(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake)
        assert camera.command("set_owned", {"owned": True}) == {"owned": True}
        assert camera.owned() is True
        assert camera.command("set_owned", {"owned": False}) == {"owned": False}
        assert camera.owned() is False
        assert fake.closed is True

    def test_set_owned_requires_a_boolean(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        with pytest.raises(CommandRejected, match="must be true or false"):
            camera.command("set_owned", {"owned": "yes"})

    def test_primary_command_is_set_owned(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        assert camera.primary_command() == "set_owned"

    def test_commands_only_offer_snapshot_once_owned(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        assert [c["name"] for c in camera.commands()] == ["set_owned"]
        camera.own()
        assert "snapshot" in [c["name"] for c in camera.commands()]


class TestMockCamera:
    def test_is_always_available(self) -> None:
        assert MockCamera().available() is True

    def test_reports_itself_as_a_simulation(self) -> None:
        assert is_simulated(MockCamera()) is True

    def test_snapshot_is_a_png(self) -> None:
        result = MockCamera().command("snapshot", {"max_width": 160})
        assert base64.b64decode(result["image_base64"]).startswith(_PNG_SIGNATURE)
        assert result["width"] == 160

    def test_successive_snapshots_differ(self) -> None:
        clock = _Clock()
        camera = MockCamera(clock=clock)
        first = camera.command("snapshot", {})["image_base64"]
        clock.advance(1.0)
        assert camera.command("snapshot", {})["image_base64"] != first

    def test_the_same_clock_gives_the_same_frame(self) -> None:
        clock = _Clock()
        camera = MockCamera(clock=clock)
        first = camera.command("snapshot", {})["image_base64"]
        assert camera.command("snapshot", {})["image_base64"] == first

    def test_state_counts_the_snapshots_taken(self) -> None:
        camera = MockCamera()
        camera.command("snapshot", {})
        camera.command("snapshot", {})
        assert camera.state()["snapshots"] == 2

    def test_an_unknown_command_is_rejected(self) -> None:
        with pytest.raises(CommandRejected, match="no command"):
            MockCamera().command("record", {})

    def test_a_width_out_of_range_is_rejected(self) -> None:
        with pytest.raises(CommandRejected, match="between"):
            MockCamera().command("snapshot", {"max_width": 9000})


class TestDetection:
    def test_the_simulation_is_registered_when_it_is_named(self) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(simulated_instruments=["camera"], psu_port="", daq_serial=""))
        provider = registry.provider("camera")
        assert provider is not None
        assert is_simulated(provider) is True

    def test_nothing_is_registered_when_the_camera_is_not_looked_for(self) -> None:
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device="", psu_port="", daq_serial=""))
        assert registry.provider("camera") is None

    def test_a_camera_that_does_not_answer_is_not_registered(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # An Allied Vision camera on the bench's own bus would otherwise be
        # registered here, and this is about the capture-node driver.
        monkeypatch.setattr("gauntlet.instruments.detect.AlviumCamera", _absent_camera)
        monkeypatch.setattr("gauntlet.instruments.detect.UvcCamera", _absent_camera)
        registry = CapabilityRegistry()
        detect_instruments(registry, Settings(camera_device="auto", psu_port="", daq_serial=""))
        assert registry.provider("camera") is None


def _absent_camera(**kwargs: Any) -> Any:
    """A driver that finds nothing, for the detection tests."""

    class _Absent:
        name = "camera"

        def available(self) -> bool:
            return False

        def close(self) -> None:
            return None

        def describe(self) -> dict[str, str]:
            return {"driver": "uvc"}

        def instance_id(self) -> str:
            return "camera0"

    return _Absent()


class _FakeDriver:
    """A scripted `VIDIOC_DQBUF` sequence, for the buffers `grab` has to filter.

    Each scripted frame is the index, byte count, flags and sequence number the
    driver reports. The last entry repeats once the script runs out, so a test
    can offer an endless stream of one kind of buffer.
    """

    def __init__(self, frames: list[tuple[int, int, int, int]]) -> None:
        self.frames = list(frames)
        self.queued: list[int] = []

    def ioctl(self, request: int, argument: Any) -> None:
        if request == v4l2.VIDIOC_DQBUF:
            index, bytesused, flags, sequence = self.frames[0]
            if len(self.frames) > 1:
                self.frames.pop(0)
            argument.index = index
            argument.bytesused = bytesused
            argument.flags = flags
            argument.sequence = sequence
        elif request == v4l2.VIDIOC_QBUF:
            self.queued.append(argument.index)


def streaming_camera(
    monkeypatch: pytest.MonkeyPatch,
    frames: list[tuple[int, int, int, int]],
    *,
    buffers: int = 4,
) -> tuple[v4l2.V4l2Camera, _FakeDriver]:
    """A camera already streaming, whose driver answers from `frames`.

    Every mapped buffer is filled with its own index so a test can tell which
    one the returned frame was copied from.
    """
    camera = v4l2.V4l2Camera(Path("/dev/video0"))
    driver = _FakeDriver(frames)
    camera._fd = 3
    camera._streaming = True
    camera._format = {
        "height": 2,
        "pixelformat": PIXELFORMAT_YUYV,
        "sizeimage": 16,
        "width": 4,
    }
    camera._maps = [bytearray([index + 1]) * 16 for index in range(buffers)]
    monkeypatch.setattr(camera, "_ioctl", driver.ioctl)
    monkeypatch.setattr(v4l2.select, "select", lambda *args: ([3], [], []))
    return camera, driver


class TestGrabBufferFiltering:
    """A frame the driver marked bad is skipped rather than reported.

    Starting a stream on the bench camera flushes the buffers that were queued
    before the sensor produced anything, one error frame per buffer, so the
    first good frame arrives fifth.
    """

    def test_returns_a_good_frame(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(2, 16, 0, 7)])
        frame = camera.grab(timeout_s=1.0)
        assert frame.sequence == 7
        assert frame.data == bytes([3]) * 16

    def test_skips_a_buffer_flagged_as_an_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(
            monkeypatch,
            [(0, 0, v4l2.BUF_FLAG_ERROR, 0), (1, 16, 0, 4)],
        )
        assert camera.grab(timeout_s=1.0).sequence == 4

    def test_skips_a_buffer_carrying_no_bytes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(0, 0, 0, 0), (1, 16, 0, 5)])
        assert camera.grab(timeout_s=1.0).sequence == 5

    def test_skips_the_flush_a_stream_start_produces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        start = [(index, 0, v4l2.BUF_FLAG_ERROR, 0) for index in range(4)]
        camera, driver = streaming_camera(monkeypatch, [*start, (0, 16, 0, 0)])
        frame = camera.grab(timeout_s=1.0)
        assert frame.data == bytes([1]) * 16
        assert driver.queued == [0, 1, 2, 3, 0]

    def test_hands_every_skipped_buffer_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = streaming_camera(
            monkeypatch,
            [(2, 0, v4l2.BUF_FLAG_ERROR, 0), (3, 16, 0, 1)],
        )
        camera.grab(timeout_s=1.0)
        assert driver.queued == [2, 3]

    def test_gives_up_on_a_stream_that_only_errors(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(0, 0, v4l2.BUF_FLAG_ERROR, 0)])
        with pytest.raises(V4l2Error, match="no frame within"):
            camera.grab(timeout_s=0.05)

    def test_gives_up_when_no_buffer_arrives(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(0, 16, 0, 0)])
        monkeypatch.setattr(v4l2.select, "select", lambda *args: ([], [], []))
        with pytest.raises(V4l2Error, match="no frame within"):
            camera.grab(timeout_s=0.05)


class _FakeMap(bytearray):
    """A mapped buffer the camera can release, standing in for `mmap.mmap`."""

    def __init__(self) -> None:
        super().__init__(16)

    def close(self) -> None:
        return None


class _StartupDriver:
    """A driver that answers everything `start` asks, and may send no frame.

    The timeout each wait was given is kept, because how long `start` is
    prepared to wait is the whole of what it adds.
    """

    def __init__(self, silent: bool) -> None:
        self.silent = silent
        self.streaming = False
        self.waits: list[float] = []

    def ioctl(self, request: int, argument: Any) -> None:
        if request == v4l2.VIDIOC_REQBUFS:
            argument.count = 2
        elif request == v4l2.VIDIOC_QUERYBUF:
            argument.length = 16
            argument.offset = 0
        elif request == v4l2.VIDIOC_STREAMON:
            self.streaming = True
        elif request == v4l2.VIDIOC_STREAMOFF:
            self.streaming = False
        elif request == v4l2.VIDIOC_DQBUF:
            argument.index = 0
            argument.bytesused = 16
            argument.flags = 0
            argument.sequence = 1

    def select(self, _read: Any, _write: Any, _error: Any, timeout: float) -> tuple[list[int], list[int], list[int]]:
        self.waits.append(timeout)
        return ([], [], []) if self.silent else ([3], [], [])


def starting_camera(monkeypatch: pytest.MonkeyPatch, *, silent: bool) -> tuple[v4l2.V4l2Camera, _StartupDriver]:
    """A camera about to be started, and the device behind it."""
    camera = v4l2.V4l2Camera(Path("/dev/video0"))
    driver = _StartupDriver(silent)
    camera._fd = 3
    camera._format = {"height": 2, "pixelformat": PIXELFORMAT_YUYV, "sizeimage": 16, "width": 4}
    monkeypatch.setattr(camera, "_ioctl", driver.ioctl)
    monkeypatch.setattr(v4l2.select, "select", driver.select)
    monkeypatch.setattr(v4l2.mmap, "mmap", lambda *args, **kwargs: _FakeMap())
    return camera, driver


class TestStreamStartup:
    """`start` means the device is streaming at its rate, not that the ioctl was accepted.

    The bench's 4K GMSL head answers VIDIOC_STREAMON at once and then sends
    nothing for anywhere between a fifth of a second and twenty, so a caller
    that began capturing on the ioctl alone raced it. It then delivers its
    first few frames seconds apart, so a caller that began on the first frame
    raced it too.
    """

    def test_a_device_sending_at_rate_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = starting_camera(monkeypatch, silent=False)
        camera.start()
        assert camera._streaming
        assert driver.streaming
        assert len(driver.waits) == v4l2._SETTLE_FRAMES

    def test_no_single_wait_outlasts_the_settling_gap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = starting_camera(monkeypatch, silent=False)
        camera.start()
        assert max(driver.waits) <= v4l2._SETTLE_GAP_S

    def test_a_silent_device_is_polled_until_the_budget_runs_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = starting_camera(monkeypatch, silent=True)
        monkeypatch.setattr(v4l2, "_STARTUP_TIMEOUT_S", 0.05)
        with pytest.raises(V4l2Error):
            camera.start()
        assert len(driver.waits) > 1

    def test_a_device_that_never_sends_is_not_left_streaming(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = starting_camera(monkeypatch, silent=True)
        monkeypatch.setattr(v4l2, "_STARTUP_TIMEOUT_S", 0.05)
        with pytest.raises(V4l2Error, match="never reached its rate"):
            camera.start()
        assert not camera._streaming
        assert not driver.streaming


def raw10_frame(width: int, height: int, red: int, green: int, blue: int) -> bytes:
    """A RAW10 RGGB frame of one flat colour, as 16-bit little-endian words."""
    even = b"".join((red if x % 2 == 0 else green).to_bytes(2, "little") for x in range(width))
    odd = b"".join((green if x % 2 == 0 else blue).to_bytes(2, "little") for x in range(width))
    return b"".join(even if y % 2 == 0 else odd for y in range(height))


class TestRaw10Detection:
    """A frame is read for what it carries, not for what the driver calls it."""

    def test_raw10_is_recognised(self) -> None:
        assert imaging.looks_like_raw10(raw10_frame(8, 8, 200, 400, 150))

    def test_a_full_range_frame_is_not_raw10(self) -> None:
        assert not imaging.looks_like_raw10(yuyv_frame(8, 8, luma=128, chroma=128))

    def test_a_sample_above_the_ceiling_settles_it(self) -> None:
        assert not imaging.looks_like_raw10((1024).to_bytes(2, "little") * 64)

    def test_an_empty_frame_is_not_raw10(self) -> None:
        assert not imaging.looks_like_raw10(b"")

    def test_auto_picks_raw10_for_a_raw_frame(self) -> None:
        frame = Frame(
            data=raw10_frame(8, 8, 200, 400, 150),
            pixelformat=PIXELFORMAT_YUYV,
            width=8,
            height=8,
            sequence=0,
        )
        assert imaging.resolve_encoding(frame, imaging.ENCODING_AUTO) == imaging.ENCODING_RAW10_RGGB

    def test_a_named_format_is_not_second_guessed(self) -> None:
        frame = Frame(
            data=raw10_frame(8, 8, 200, 400, 150),
            pixelformat=PIXELFORMAT_YUYV,
            width=8,
            height=8,
            sequence=0,
        )
        assert imaging.resolve_encoding(frame, imaging.ENCODING_YUYV) == imaging.ENCODING_YUYV

    def test_an_unknown_format_is_refused(self) -> None:
        frame = Frame(data=b"", pixelformat=PIXELFORMAT_YUYV, width=0, height=0, sequence=0)
        with pytest.raises(ImageError, match="unknown camera format"):
            imaging.resolve_encoding(frame, "rgb24")


class TestRaw10Conversion:
    """Binning a Bayer cell is the demosaic, and the channels land in order."""

    def test_a_cell_becomes_one_pixel(self) -> None:
        pixels, width, height = imaging.raw10_rggb_to_rgb(raw10_frame(4, 4, 400, 800, 200), 4, 4)
        assert (width, height) == (2, 2)
        assert bytes(pixels[:3]) == bytes([100, 200, 50])

    def test_both_greens_are_averaged(self) -> None:
        data = bytearray(raw10_frame(2, 2, 400, 800, 200))
        # The second green of the cell, on the row below.
        data[4:6] = (400).to_bytes(2, "little")
        pixels, _, _ = imaging.raw10_rggb_to_rgb(bytes(data), 2, 2)
        assert pixels[1] == 150

    def test_step_counts_cells(self) -> None:
        _, width, height = imaging.raw10_rggb_to_rgb(raw10_frame(8, 8, 400, 800, 200), 8, 8, step=2)
        assert (width, height) == (2, 2)

    def test_a_short_frame_is_refused(self) -> None:
        with pytest.raises(ImageError, match="raw frame is"):
            imaging.raw10_rggb_to_rgb(b"\x00" * 8, 4, 4)

    def test_step_below_one_is_refused(self) -> None:
        with pytest.raises(ImageError, match="step must be at least 1"):
            imaging.raw10_rggb_to_rgb(raw10_frame(4, 4, 400, 800, 200), 4, 4, step=0)


class TestWhiteBalance:
    """Raw output is green-heavy because a cell samples green twice."""

    def test_a_green_cast_is_levelled(self) -> None:
        pixels = bytearray([60, 120, 60] * 16)
        imaging.white_balance(pixels)
        assert pixels[0] == pixels[1] == pixels[2]

    def test_a_neutral_image_is_left_alone(self) -> None:
        pixels = bytearray([100, 100, 100] * 16)
        imaging.white_balance(pixels)
        assert bytes(pixels) == bytes([100, 100, 100] * 16)

    def test_an_empty_image_is_left_alone(self) -> None:
        pixels = bytearray()
        imaging.white_balance(pixels)
        assert not pixels

    def test_an_empty_channel_is_left_alone(self) -> None:
        pixels = bytearray([0, 120, 60] * 16)
        imaging.white_balance(pixels)
        assert pixels[0] == 0


class TestEncodeRaw10Frame:
    """The whole path, from a raw frame to a written image."""

    def test_a_raw_frame_reports_the_format_it_was_read_as(self) -> None:
        frame = Frame(
            data=raw10_frame(16, 16, 400, 800, 200),
            pixelformat=PIXELFORMAT_YUYV,
            width=16,
            height=16,
            sequence=0,
        )
        payload, measured = imaging.encode_frame(frame, max_width=8)
        assert measured["encoding"] == imaging.ENCODING_RAW10_RGGB
        assert png_size(payload) == (8, 8)

    def test_a_yuyv_frame_still_reports_yuyv(self) -> None:
        frame = Frame(
            data=yuyv_frame(16, 16),
            pixelformat=PIXELFORMAT_YUYV,
            width=16,
            height=16,
            sequence=0,
        )
        _, measured = imaging.encode_frame(frame, max_width=16)
        assert measured["encoding"] == imaging.ENCODING_YUYV


class _FakeLink:
    """A GMSL link that records whether the video was running when it was read."""

    def __init__(self, camera: _FakeCamera) -> None:
        self.camera = camera
        self.closed = False
        self.streaming_when_read: list[bool] = []
        self.streaming_when_scanned = True
        self.locked = True
        self.link_error = False
        self.chips = [0x84]
        self.open_error: Exception | None = None
        self.read_error: Exception | None = None

    def open(self) -> None:
        if self.open_error is not None:
            raise self.open_error

    def close(self) -> None:
        self.closed = True

    def scan(self) -> list[int]:
        self.streaming_when_scanned = self.camera.started
        return self.chips

    def identity(self) -> dict[str, str]:
        return {"uuid": "fake"}

    def status(self, address: int) -> gmsl.ChipStatus:
        self.streaming_when_read.append(self.camera.started)
        if self.read_error is not None:
            raise self.read_error
        return gmsl.ChipStatus(
            address=address,
            decode_errors_a=0,
            decode_errors_b=0,
            dev_id=0xB7,
            dev_rev=0x06,
            idle_errors=0,
            link_error=False,
            locked=True,
        )

    def link_state(self, address: int) -> tuple[bool, bool]:
        self.streaming_when_read.append(self.camera.started)
        if self.read_error is not None:
            raise self.read_error
        return self.locked, self.link_error


def linked_camera(monkeypatch: pytest.MonkeyPatch, fake: _FakeCamera, **kwargs: Any) -> tuple[UvcCamera, _FakeLink]:
    """An owned camera with a stand-in GMSL link behind its node."""
    link = _FakeLink(fake)
    monkeypatch.setattr("gauntlet.instruments.uvc_camera.GmslLink", lambda node: link)
    camera = camera_with(fake, **kwargs)
    assert camera.own() is True
    return camera, link


class TestLinkReadsAndVideoTakeTurns:
    """The chips and the video cannot share the connection, so they alternate.

    The adapter offers 4K YUYV only, about 2.6Gbps, which leaves no room for
    control transfers: a read taken mid-stream stops the video for anywhere
    between a fraction of a second and over a minute.
    """

    def test_the_stream_is_stopped_while_the_chips_are_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, link = linked_camera(monkeypatch, fake)

        camera.command("link_status", {})

        assert link.streaming_when_read == [False]
        assert fake.started is True

    def test_the_chips_are_scanned_before_the_stream_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        _, link = linked_camera(monkeypatch, fake)

        # Finding the chips is 127 transfers, so it costs the most of any read.
        assert link.streaming_when_scanned is False

    def test_a_stream_that_will_not_restart_drops_the_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, _ = linked_camera(monkeypatch, fake)
        monkeypatch.setattr(fake, "start", _raise_start)

        reading = camera.command("link_status", {})

        assert reading["error"]
        assert camera.owned() is False


class TestLinkRegister:
    """One chip's lock state, read without touching the other five registers."""

    def test_the_stream_is_stopped_while_the_register_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, link = linked_camera(monkeypatch, fake)

        reading = camera.command("link_register", {"address": "0x84"})

        assert link.streaming_when_read == [False]
        assert fake.started is True
        assert reading == {"address": "0x84", "error": "", "link_error": False, "locked": True}

    def test_an_unlocked_chip_is_reported_as_such(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, link = linked_camera(monkeypatch, fake)
        link.locked = False

        reading = camera.command("link_register", {"address": "0x84"})

        assert reading["locked"] is False

    def test_an_address_nothing_answered_at_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, _ = linked_camera(monkeypatch, fake)

        with pytest.raises(CommandRejected, match="0x50"):
            camera.command("link_register", {"address": "0x50"})

    def test_a_camera_with_no_link_behind_it_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        link = _FakeLink(fake)
        link.streaming_when_scanned = True

        class _NoChips(_FakeLink):
            def scan(self) -> list[int]:
                return []

        monkeypatch.setattr("gauntlet.instruments.uvc_camera.GmslLink", lambda node: _NoChips(fake))
        camera = camera_with(fake)
        assert camera.own() is True

        with pytest.raises(CommandRejected, match="not behind a GMSL adapter"):
            camera.command("link_register", {"address": "0x84"})

    def test_a_stream_that_will_not_restart_drops_the_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, _ = linked_camera(monkeypatch, fake)
        monkeypatch.setattr(fake, "start", _raise_start)

        reading = camera.command("link_register", {"address": "0x84"})

        assert reading["error"]
        assert camera.owned() is False

    def test_a_malformed_address_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, _ = linked_camera(monkeypatch, fake)

        with pytest.raises(CommandRejected, match="not a hex address"):
            camera.command("link_register", {"address": "not-hex"})

    def test_write_returns_the_reading_itself_rather_than_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A suite reaches this through `write`, not `command`: the same gap that
        once left `link_status` and `snapshot` silently swapped for `state()`
        could as easily swallow this one, so it is worth its own check."""
        fake = _FakeCamera(Path("/dev/video0"))
        camera, _ = linked_camera(monkeypatch, fake)

        result = camera.write({"command": "link_register", "args": {"address": "0x84"}})

        assert result == {"address": "0x84", "error": "", "link_error": False, "locked": True}


def _raise_start() -> None:
    """A device whose stream will not come back."""
    raise V4l2Error("/dev/video0: streaming started but no frame arrived")


class _FakeXu:
    """A stand-in extension unit holding one register map per I2C address."""

    def __init__(self, devices: dict[int, dict[int, int]], identity: bytes = b"") -> None:
        self.devices = devices
        self.identity = identity
        self.writes = 0
        self._pending: tuple[int, int] | None = None

    def ioctl(self, fd: int, request: int, query: Any) -> None:
        length = query.size
        data = ctypes.cast(query.data, ctypes.POINTER(ctypes.c_uint8 * length)).contents
        if query.selector == gmsl.XU_UUID_HWFW_REV:
            for index, value in enumerate(self.identity[:length]):
                data[index] = value
            return
        address = (data[2] << 8) | data[3]
        register = (data[4] << 8) | data[5]
        if data[0] & 0x80:
            self.writes += 1
            return
        if query.query == 0x01:
            self._pending = (address, register)
            return
        found = self.devices.get(address, {})
        if register not in found:
            raise OSError(6, "No such device or address")
        data[6] = found[register]


def gmsl_link(monkeypatch: pytest.MonkeyPatch, fake: _FakeXu) -> gmsl.GmslLink:
    """A link whose transfers land on the fake rather than on a device."""
    link = gmsl.GmslLink(Path("/dev/video0"))
    link._fd = 7
    monkeypatch.setattr(gmsl.fcntl, "ioctl", fake.ioctl)
    return link


def chip(dev_id: int, *, locked: bool = True, decode_a: int = 0, decode_b: int = 0, idle: int = 0) -> dict[int, int]:
    """One chip's registers, as the scan and the status read them."""
    return {
        gmsl.REG_ADDRESS: 0,
        gmsl.REG_DEV_ID: dev_id,
        gmsl.REG_DEV_REV: 0x06,
        gmsl.REG_CTRL3: 0x08 if locked else 0x00,
        gmsl.REG_DECODE_ERRORS_A: decode_a,
        gmsl.REG_DECODE_ERRORS_B: decode_b,
        gmsl.REG_IDLE_ERRORS: idle,
    }


class TestGmslLink:
    """The chips behind the camera, read over the extension unit."""

    def test_a_chip_is_found_by_its_own_address(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registers = chip(0xB7)
        registers[gmsl.REG_ADDRESS] = 0x84
        link = gmsl_link(monkeypatch, _FakeXu({0x84: registers}))
        assert link.scan() == [0x84]

    def test_a_register_reading_back_something_else_is_not_a_chip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        registers = chip(0xB7)
        registers[gmsl.REG_ADDRESS] = 0x00
        link = gmsl_link(monkeypatch, _FakeXu({0x84: registers}))
        assert link.scan() == []

    def test_status_reports_lock_and_counters(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = gmsl_link(monkeypatch, _FakeXu({0x84: chip(0xB7, decode_a=3, idle=1)}))
        status = link.status(0x84)
        assert status.locked
        assert status.decode_errors_a == 3
        assert status.total_errors == 4

    def test_an_unlocked_chip_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = gmsl_link(monkeypatch, _FakeXu({0x84: chip(0xB7, locked=False)}))
        assert not link.status(0x84).locked

    def test_link_state_reads_only_the_control_register(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeXu({0x84: chip(0xB7)})
        link = gmsl_link(monkeypatch, fake)
        locked, link_error = link.link_state(0x84)
        assert locked
        assert not link_error

    def test_link_state_agrees_with_status_on_an_unlocked_chip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = gmsl_link(monkeypatch, _FakeXu({0x84: chip(0xB7, locked=False)}))
        locked, _ = link.link_state(0x84)
        assert not locked

    def test_a_counter_at_its_ceiling_is_saturated(self, monkeypatch: pytest.MonkeyPatch) -> None:
        link = gmsl_link(monkeypatch, _FakeXu({0x84: chip(0xB7, decode_a=0xFF)}))
        assert link.status(0x84).saturated

    def test_reading_never_writes(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A write can drop the link, which mid-irradiation reads as a result."""
        fake = _FakeXu({0x84: chip(0xB7)})
        link = gmsl_link(monkeypatch, fake)
        link.scan()
        link.status(0x84)
        assert fake.writes == 0

    def test_identity_is_split_into_its_fields(self, monkeypatch: pytest.MonkeyPatch) -> None:
        raw = bytes([0x02, 0x21, 0x78, 0x08]) + b"8a06621b-9041-4ff1-afcc-9f9ea482b59f-20260803"
        link = gmsl_link(monkeypatch, _FakeXu({}, identity=raw))
        identity = link.identity()
        assert identity["hardware_revision"] == "258"
        assert identity["firmware_revision"] == "2168"
        assert identity["uuid"].startswith("8a06621b-9041-4ff1")

    def test_a_closed_link_is_refused(self) -> None:
        with pytest.raises(gmsl.GmslError, match="not open"):
            gmsl.GmslLink(Path("/dev/video0")).read_register(0x84, 0x00)


def _scripted_select(monkeypatch: pytest.MonkeyPatch, readiness: list[bool]) -> None:
    """Answer each `select` call from `readiness`, repeating its last entry."""
    remaining = list(readiness)

    def _select(*_args: Any) -> tuple[list[int], list[int], list[int]]:
        ready = remaining[0]
        if len(remaining) > 1:
            remaining.pop(0)
        return ([3], [], []) if ready else ([], [], [])

    monkeypatch.setattr(v4l2.select, "select", _select)


class TestStreamSettling:
    """A stream counts as running only once frames arrive back to back.

    The bench's GMSL head delivers its first few frames seconds apart and only
    then reaches its rate, so `start()` waits for the gaps to close. Without
    that wait the first samples of a run time out against a camera that had
    technically started.
    """

    def test_settles_on_consecutive_prompt_frames(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = streaming_camera(monkeypatch, [(0, 16, 0, 1)])
        camera._settle()
        assert len(driver.queued) == v4l2._SETTLE_FRAMES

    def test_a_late_frame_restarts_the_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = streaming_camera(monkeypatch, [(0, 16, 0, 1)])
        _scripted_select(monkeypatch, [True, True, False, True, True, True])
        camera._settle()
        assert len(driver.queued) == 2 + v4l2._SETTLE_FRAMES

    def test_the_flush_a_start_produces_does_not_count(self, monkeypatch: pytest.MonkeyPatch) -> None:
        flush = [(index, 0, v4l2.BUF_FLAG_ERROR, 0) for index in range(4)]
        camera, driver = streaming_camera(monkeypatch, [*flush, (0, 16, 0, 1)])
        camera._settle()
        assert len(driver.queued) == 4 + v4l2._SETTLE_FRAMES

    def test_a_stream_that_never_settles_times_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(v4l2, "_STARTUP_TIMEOUT_S", 0.05)
        camera, _ = streaming_camera(monkeypatch, [(0, 16, 0, 1)])
        _scripted_select(monkeypatch, [False])
        with pytest.raises(V4l2Error, match="frames in a row"):
            camera._settle()

    def test_a_stream_that_only_errors_never_settles(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(v4l2, "_STARTUP_TIMEOUT_S", 0.05)
        camera, _ = streaming_camera(monkeypatch, [(0, 0, v4l2.BUF_FLAG_ERROR, 0)])
        with pytest.raises(V4l2Error, match="frames in a row"):
            camera._settle()


def unlinked_camera(monkeypatch: pytest.MonkeyPatch, fake: _FakeCamera, **kwargs: Any) -> UvcCamera:
    """A camera whose node has no GMSL chips behind it, so no real link is probed."""
    link = _FakeLink(fake)
    link.chips = []
    monkeypatch.setattr("gauntlet.instruments.uvc_camera.GmslLink", lambda node: link)
    return camera_with(fake, **kwargs)


class TestUvcCameraSurface:
    """What the panel and a suite read off the provider, owned or not."""

    def test_an_explicit_node_is_present_only_while_it_exists(self, tmp_path: Path) -> None:
        node = tmp_path / "video9"
        camera = UvcCamera(device=str(node))
        assert camera.available() is False
        assert camera.describe()["unavailable_reason"] == f"{node}: not present"
        node.write_bytes(b"")
        assert camera.available() is True

    def test_no_capture_node_at_all_is_reported(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("gauntlet.instruments.uvc_camera.capture_devices", lambda: [])
        camera = UvcCamera(device="")
        assert camera.available() is False
        assert camera.own() is False
        assert camera.describe()["unavailable_reason"] == "no /dev/video* node is present"

    def test_an_owned_camera_is_available_without_looking_again(self, monkeypatch: pytest.MonkeyPatch) -> None:
        presence_checks: list[str] = []

        def presence(device: str) -> bool:
            presence_checks.append(device)
            return True

        fake = _FakeCamera(Path("/dev/video0"))
        camera = unlinked_camera(monkeypatch, fake)
        camera._presence = presence
        camera.own()
        assert camera.available() is True
        assert presence_checks == []

    def test_disowning_releases_the_device(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = unlinked_camera(monkeypatch, fake)
        camera.own()
        camera.disown()
        assert fake.closed is True
        assert camera.owned() is False

    def test_a_device_that_fails_to_close_is_still_released(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.close_error = V4l2Error("/dev/video0: device has gone")
        camera = unlinked_camera(monkeypatch, fake)
        camera.own()
        camera.close()
        assert camera.owned() is False

    def test_connection_names_the_node_and_the_bus(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        assert camera.connection() == "no camera"
        camera.own()
        assert camera.connection() == "/dev/video0 usb-0000:07:00.1-4.4"

    def test_the_instance_is_the_one_it_was_built_with(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), instance="camera.dut")
        assert camera.instance_id() == "camera.dut"

    def test_read_is_the_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        camera.own()
        assert camera.read() == camera.state()

    def test_the_link_readouts_appear_only_behind_an_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plain = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        plain.own()
        assert not [entry for entry in plain.readouts() if entry["key"].startswith("link.")]

        linked, _ = linked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        assert [entry["key"] for entry in linked.readouts() if entry["key"].startswith("link.")] == [
            "link.errors",
            "link.total_errors",
            "link.locked",
        ]

    def test_every_readout_names_a_path_that_state_carries(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = linked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")), warmup_frames=0)
        camera.command("snapshot", {})
        state = camera.state()
        for entry in camera.readouts():
            cursor: Any = state
            for part in entry["key"].split("."):
                assert part in cursor, f"{entry['key']} is not in state()"
                cursor = cursor[part]

    def test_link_status_is_offered_only_behind_an_adapter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        plain = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        plain.own()
        assert "link_status" not in [row["name"] for row in plain.commands()]

        linked, _ = linked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        assert "link_status" in [row["name"] for row in linked.commands()]

    def test_a_write_of_anything_but_a_reading_answers_with_the_state(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        answered = camera.write({"command": "set_owned", "args": {"owned": True}})
        assert answered["streaming"] is True
        assert answered["format"]["fourcc"] == "YUYV"

    def test_the_key_on_an_unavailable_camera_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.open_error = V4l2Error("/dev/video0: device busy")
        camera = unlinked_camera(monkeypatch, fake)
        with pytest.raises(CommandRejected, match="unavailable: /dev/video0: device busy"):
            camera.command("set_owned", {"owned": True})


class TestUvcArguments:
    """What the panel's presets and a suite's numbers turn into."""

    def test_the_full_preset_is_the_whole_frame(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.width = 32
        camera = unlinked_camera(monkeypatch, fake, warmup_frames=0)
        camera.own()
        assert camera.command("snapshot", {"max_width": "Full"})["width"] == 32

    def test_a_preset_named_by_its_width_caps_the_picture(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.width = 32
        camera = unlinked_camera(monkeypatch, fake, warmup_frames=0)
        camera.own()
        assert camera.command("snapshot", {"max_width": "16"})["width"] == 16

    def test_a_warmup_given_as_a_flag_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        camera.own()
        with pytest.raises(CommandRejected, match="'warmup' must be a number"):
            camera.command("snapshot", {"warmup": True})

    def test_an_address_that_is_not_a_string_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = linked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        with pytest.raises(CommandRejected, match="must be a string"):
            camera.command("link_register", {"address": 0x84})


class TestUvcStreamStats:
    """A burst of frames read back to back, for a suite measuring the link."""

    def test_a_burst_is_measured_at_the_default_length(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = unlinked_camera(monkeypatch, fake)
        camera.own()
        assert camera.command("stream_stats", {})["fps"] == 19.0
        assert fake.bursts == [8]

    def test_write_answers_with_the_measurement(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        camera.own()
        result = camera.write({"command": "stream_stats", "args": {"frames": 30}})
        assert result == {"dropped": 0.0, "fps": 19.0, "frames": 30.0}

    def test_a_burst_too_short_to_time_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera = unlinked_camera(monkeypatch, _FakeCamera(Path("/dev/video0")))
        camera.own()
        with pytest.raises(CommandRejected, match="between 2 and 120"):
            camera.command("stream_stats", {"frames": 1})

    def test_a_camera_that_stops_answering_is_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = unlinked_camera(monkeypatch, fake)
        camera.own()
        fake.grab_error = V4l2Error("/dev/video0: device has gone")
        with pytest.raises(CommandRejected, match="device has gone"):
            camera.command("stream_stats", {})
        assert fake.closed is True
        assert camera.owned() is False


class TestUvcLinkFailures:
    """A link that stops answering is a reading, not a crash."""

    def test_a_link_that_will_not_open_leaves_a_plain_camera(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        link = _FakeLink(fake)
        link.open_error = gmsl.GmslError("/dev/video0: no extension unit")
        monkeypatch.setattr("gauntlet.instruments.uvc_camera.GmslLink", lambda node: link)
        camera = camera_with(fake)
        assert camera.own() is True
        assert link.closed is True
        with pytest.raises(CommandRejected, match="not behind a GMSL adapter"):
            camera.command("link_status", {})

    def test_the_panel_poll_reads_the_chips_at_most_once_per_interval(self, monkeypatch: pytest.MonkeyPatch) -> None:
        clock = _Clock()
        fake = _FakeCamera(Path("/dev/video0"))
        camera, link = linked_camera(monkeypatch, fake, clock=clock, link_interval_s=15.0)

        assert camera.state()["link"]["locked"] is True
        camera.state()
        assert len(link.streaming_when_read) == 1

        clock.advance(15.0)
        camera.state()
        assert len(link.streaming_when_read) == 2

    def test_a_status_read_that_fails_is_reported_in_the_same_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, link = linked_camera(monkeypatch, fake)
        link.read_error = gmsl.GmslError("0x84: no answer")

        reading = camera.command("link_status", {})

        assert reading == {"chips": {}, "error": "0x84: no answer", "identity": {}, "locked": False, "total_errors": 0}
        assert fake.started is True
        assert camera.owned() is True

    def test_a_register_read_that_fails_restarts_the_stream(self, monkeypatch: pytest.MonkeyPatch) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera, link = linked_camera(monkeypatch, fake)
        link.read_error = OSError(5, "Input/output error")

        reading = camera.command("link_register", {"address": "0x84"})

        assert reading["error"] == "[Errno 5] Input/output error"
        assert reading["locked"] is False
        assert fake.started is True
        assert camera.owned() is True


class TestMockCameraSurface:
    def test_it_offers_only_a_snapshot(self) -> None:
        camera = MockCamera()
        assert [row["name"] for row in camera.commands()] == ["snapshot"]
        assert camera.primary_command() == "snapshot"

    def test_it_says_it_is_simulated(self) -> None:
        assert MockCamera().connection() == "simulated"

    def test_the_instance_is_the_one_it_was_built_with(self) -> None:
        assert MockCamera(instance="camera.ref").instance_id() == "camera.ref"

    def test_read_is_the_state(self) -> None:
        camera = MockCamera()
        camera.command("snapshot", {})
        assert camera.read() == camera.state()

    def test_every_readout_names_a_path_that_state_carries(self) -> None:
        camera = MockCamera()
        camera.command("snapshot", {"max_width": 160})
        state = camera.state()
        for entry in camera.readouts():
            cursor: Any = state
            for part in entry["key"].split("."):
                assert part in cursor, f"{entry['key']} is not in state()"
                cursor = cursor[part]

    def test_a_write_of_a_snapshot_answers_with_the_picture(self) -> None:
        result = MockCamera().write({"command": "snapshot", "args": {"max_width": 160}})
        assert base64.b64decode(result["image_base64"]).startswith(_PNG_SIGNATURE)


class TestImagingEdges:
    def test_a_live_frame_is_written_as_a_jpeg(self) -> None:
        frame = Frame(yuyv_frame(8, 8), PIXELFORMAT_YUYV, 8, 8, sequence=1)
        payload, measured = encode_frame(frame, lossy=True)
        assert payload.startswith(b"\xff\xd8")
        assert measured["width"] == 8

    def test_an_empty_image_measures_as_nothing(self) -> None:
        assert measure(bytearray(), 0, 0) == {"mean_luma": 0.0, "sharpness": 0.0}

    def test_a_colour_past_the_top_of_the_range_is_held_at_full(self) -> None:
        pixels, _, _ = yuyv_to_rgb(bytes((255, 255, 255, 255)), 2, 1)
        assert pixels[0] == 255
        assert pixels[2] == 255


class TestGmslLinkOpening:
    def test_a_node_that_will_not_open_is_a_link_error(self, tmp_path: Path) -> None:
        with pytest.raises(gmsl.GmslError, match="No such file"):
            gmsl.GmslLink(tmp_path / "video9").open()

    def test_opening_an_open_link_keeps_its_descriptor(self, tmp_path: Path) -> None:
        link = gmsl.GmslLink(tmp_path / "video9")
        link._fd = 7
        link.open()
        assert link._fd == 7


class _FakeNode:
    """The ioctls a capture node answers while being opened and described."""

    def __init__(self) -> None:
        self.capabilities = 0
        self.device_caps = v4l2.CAP_VIDEO_CAPTURE | v4l2.CAP_STREAMING
        self.errors: dict[int, int] = {}
        self.formats = [(PIXELFORMAT_YUYV, b"YUYV 4:2:2"), (0x3231564E, b"Y/UV 4:2:0")]

    def ioctl(self, fd: int, request: int, argument: Any) -> None:
        if request in self.errors:
            code = self.errors[request]
            raise OSError(code, errno.errorcode[code])
        if request == v4l2.VIDIOC_QUERYCAP:
            argument.driver = b"uvcvideo"
            argument.card = b"LI-IMX728"
            argument.bus_info = b"usb-0000:07:00.1-4.4"
            argument.capabilities = self.capabilities
            argument.device_caps = self.device_caps
        elif request == v4l2.VIDIOC_G_FMT:
            argument.pix.width = 3840
            argument.pix.height = 2160
            argument.pix.pixelformat = PIXELFORMAT_YUYV
            argument.pix.bytesperline = 7680
            argument.pix.sizeimage = 3840 * 2160 * 2
        elif request == v4l2.VIDIOC_ENUM_FMT:
            if argument.index >= len(self.formats):
                raise OSError(errno.EINVAL, "Invalid argument")
            argument.pixelformat, argument.description = self.formats[argument.index]


def capture_node(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[v4l2.V4l2Camera, _FakeNode]:
    """A camera on a plain file, whose ioctls the fake answers."""
    path = tmp_path / "video0"
    path.write_bytes(b"")
    node = _FakeNode()
    monkeypatch.setattr("gauntlet.instruments.v4l2.fcntl.ioctl", node.ioctl)
    return v4l2.V4l2Camera(path), node


class TestV4l2Opening:
    """Opening a node confirms it can stream before anything relies on it."""

    def test_opening_reads_the_format_in_force(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        camera, _ = capture_node(monkeypatch, tmp_path)
        camera.open()
        assert camera.path == tmp_path / "video0"
        assert camera.format() == {
            "bytesperline": 7680,
            "fourcc": "YUYV",
            "height": 2160,
            "pixelformat": PIXELFORMAT_YUYV,
            "sizeimage": 3840 * 2160 * 2,
            "width": 3840,
        }

    def test_opening_an_open_node_keeps_its_descriptor(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        camera, _ = capture_node(monkeypatch, tmp_path)
        camera.open()
        descriptor = camera._fd
        camera.open()
        assert camera._fd == descriptor

    def test_older_drivers_that_report_no_device_caps_still_open(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        camera, node = capture_node(monkeypatch, tmp_path)
        node.capabilities, node.device_caps = node.device_caps, 0
        camera.open()
        assert camera.format()["width"] == 3840

    def test_a_node_that_does_not_capture_is_refused_and_released(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        camera, node = capture_node(monkeypatch, tmp_path)
        node.device_caps = v4l2.CAP_STREAMING
        with pytest.raises(V4l2Error, match="not a video capture device"):
            camera.open()
        with pytest.raises(V4l2Error, match="not open"):
            camera.describe()

    def test_a_node_that_cannot_stream_is_refused(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        camera, node = capture_node(monkeypatch, tmp_path)
        node.device_caps = v4l2.CAP_VIDEO_CAPTURE
        with pytest.raises(V4l2Error, match="does not support streaming"):
            camera.open()
        assert camera.format() == {}

    def test_a_refused_ioctl_names_the_reason(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        camera, node = capture_node(monkeypatch, tmp_path)
        node.errors[v4l2.VIDIOC_G_FMT] = errno.EBUSY
        with pytest.raises(V4l2Error, match="another process is streaming"):
            camera.open()

    def test_a_missing_node_names_the_reason(self, tmp_path: Path) -> None:
        with pytest.raises(V4l2Error, match="No such file or directory"):
            v4l2.V4l2Camera(tmp_path / "video9").open()

    def test_describe_is_what_the_driver_calls_itself(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        camera, _ = capture_node(monkeypatch, tmp_path)
        camera.open()
        assert camera.describe() == {
            "bus_info": "usb-0000:07:00.1-4.4",
            "card": "LI-IMX728",
            "driver": "uvcvideo",
        }

    def test_formats_lists_every_offer_and_marks_the_usable_ones(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        camera, _ = capture_node(monkeypatch, tmp_path)
        camera.open()
        assert camera.formats() == [
            {"description": "YUYV 4:2:2", "fourcc": "YUYV", "pixelformat": PIXELFORMAT_YUYV, "supported": True},
            {"description": "Y/UV 4:2:0", "fourcc": "NV12", "pixelformat": 0x3231564E, "supported": False},
        ]

    def test_closing_a_closed_node_does_nothing(self) -> None:
        camera = v4l2.V4l2Camera(Path("/dev/video0"))
        camera.close()
        assert camera._fd is None

    def test_a_node_that_has_gone_is_still_released(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        camera, node = capture_node(monkeypatch, tmp_path)
        camera.open()
        camera._streaming = True
        node.errors[v4l2.VIDIOC_STREAMOFF] = errno.ENODEV
        camera.close()
        assert camera._fd is None
        assert camera._streaming is False
        assert camera.format() == {}

    def test_a_stream_that_will_not_stop_still_unmaps_its_buffers(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        camera, node = capture_node(monkeypatch, tmp_path)
        camera.open()
        region = _Region()
        camera._maps = [region]  # type: ignore[list-item]
        camera._streaming = True
        node.errors[v4l2.VIDIOC_STREAMOFF] = errno.ENODEV
        with pytest.raises(V4l2Error, match="device has gone"):
            camera.stop()
        assert region.closed is True
        assert camera._maps == []


class _Region:
    """Stands in for one mapped buffer, recording whether it was released."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class TestCaptureDevices:
    def test_nodes_come_back_in_the_order_the_kernel_numbered_them(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for name in ("video10", "video2", "video0", "video"):
            (tmp_path / name).write_bytes(b"")
        monkeypatch.setattr(v4l2, "Path", lambda _root: tmp_path)
        assert [path.name for path in v4l2.capture_devices()] == ["video0", "video2", "video10"]

    def test_a_node_not_named_by_number_is_skipped(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        for name in ("video1", "videofoo", "video0"):
            (tmp_path / name).write_bytes(b"")
        monkeypatch.setattr(v4l2, "Path", lambda _root: tmp_path)
        assert [path.name for path in v4l2.capture_devices()] == ["video0", "video1"]


class TestV4l2Streaming:
    def test_starting_a_running_stream_does_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, driver = streaming_camera(monkeypatch, [(0, 16, 0, 1)])
        camera.start()
        assert driver.queued == []

    def test_a_driver_that_grants_no_buffers_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = starting_camera(monkeypatch, silent=False)

        def grant_nothing(request: int, argument: Any) -> None:
            if request == v4l2.VIDIOC_REQBUFS:
                argument.count = 0

        monkeypatch.setattr(camera, "_ioctl", grant_nothing)
        with pytest.raises(V4l2Error, match="granted no buffers"):
            camera.start()
        assert camera._streaming is False

    def test_grabbing_before_streaming_is_refused(self) -> None:
        with pytest.raises(V4l2Error, match="not streaming"):
            v4l2.V4l2Camera(Path("/dev/video0")).grab()


class TestMeasureStream:
    """A burst read back to back, so the rate and the gaps are the link's own."""

    def test_the_frames_and_the_gaps_between_them_are_counted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(0, 16, 0, 10), (1, 16, 0, 11), (2, 16, 0, 14)])
        _scripted_select(monkeypatch, [False, True])
        measured = camera.measure_stream(frames=3, timeout_s=1.0)
        assert measured["frames"] == 3.0
        assert measured["corrupt"] == 0.0
        # Sequence 12 and 13 never arrived.
        assert measured["dropped"] == 2.0
        # The first frame only opens the timing, so its bytes are not counted.
        assert measured["bytes"] == 32.0

    def test_corrupt_frames_are_counted_rather_than_timed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frames = [(0, 0, v4l2.BUF_FLAG_ERROR, 0), (1, 0, 0, 0), (2, 16, 0, 5), (3, 16, 0, 6)]
        camera, _ = streaming_camera(monkeypatch, frames)
        _scripted_select(monkeypatch, [False, True])
        measured = camera.measure_stream(frames=2, timeout_s=1.0)
        assert measured["corrupt"] == 2.0
        assert measured["frames"] == 2.0
        assert measured["dropped"] == 0.0

    def test_a_corrupt_frame_inside_the_burst_is_not_also_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frames = [(0, 16, 0, 5), (1, 0, v4l2.BUF_FLAG_ERROR, 6), (2, 16, 0, 7)]
        camera, _ = streaming_camera(monkeypatch, frames)
        _scripted_select(monkeypatch, [False, True])
        measured = camera.measure_stream(frames=2, timeout_s=1.0)
        assert measured["corrupt"] == 1.0
        assert measured["dropped"] == 0.0

    def test_a_gap_beside_a_corrupt_frame_is_still_dropped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        frames = [(0, 16, 0, 5), (1, 0, v4l2.BUF_FLAG_ERROR, 6), (2, 16, 0, 9)]
        camera, _ = streaming_camera(monkeypatch, frames)
        _scripted_select(monkeypatch, [False, True])
        measured = camera.measure_stream(frames=2, timeout_s=1.0)
        assert measured["corrupt"] == 1.0
        # Sequence 7 and 8 never arrived.
        assert measured["dropped"] == 2.0

    def test_the_backlog_is_drained_before_timing_starts(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The queue sat full while nothing read it, so the jump from 2 to 100
        # belongs to the caller's pause and not to the link.
        frames = [(0, 16, 0, 1), (1, 16, 0, 2), (2, 16, 0, 100), (3, 16, 0, 101)]
        camera, driver = streaming_camera(monkeypatch, frames)
        _scripted_select(monkeypatch, [True, True, True, True, False, True])
        measured = camera.measure_stream(frames=2, timeout_s=1.0)
        assert measured["dropped"] == 0.0
        assert measured["frames"] == 2.0
        assert driver.queued == [0, 1, 2, 3]

    def test_a_silent_stream_measures_as_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(0, 16, 0, 1)])
        _scripted_select(monkeypatch, [True, False])
        measured = camera.measure_stream(frames=4, timeout_s=0.05)
        assert measured == {
            "bytes": 0.0,
            "corrupt": 0.0,
            "dropped": 0.0,
            "elapsed_s": 0.0,
            "fps": 0.0,
            "frames": 0.0,
            "mbps": 0.0,
        }

    def test_measuring_before_streaming_is_refused(self) -> None:
        with pytest.raises(V4l2Error, match="not streaming"):
            v4l2.V4l2Camera(Path("/dev/video0")).measure_stream()

    def test_a_burst_of_one_frame_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        camera, _ = streaming_camera(monkeypatch, [(0, 16, 0, 1)])
        with pytest.raises(V4l2Error, match="at least two frames"):
            camera.measure_stream(frames=1)


class TestV4l2Reasons:
    """An errno turned into what the operator does about it."""

    @pytest.mark.parametrize(
        ("code", "reason"),
        [
            (errno.EPERM, "device cgroup"),
            (errno.EACCES, "not in the 'video' group"),
            (errno.EBUSY, "device busy"),
            (errno.ENODEV, "device has gone"),
            (errno.ENOENT, "No such file or directory"),
        ],
    )
    def test_each_errno_reads_as_its_cause(self, code: int, reason: str) -> None:
        assert reason in v4l2._reason(OSError(code, "raw"))
