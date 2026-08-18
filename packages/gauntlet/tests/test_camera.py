"""The camera driver, its simulation, and the image encoder behind both.

Every test here runs against a stand-in for the device, so none of them needs
a camera attached.
"""

from __future__ import annotations

import base64
import struct
import zlib
from pathlib import Path
from typing import Any

import pytest

from gauntlet.capabilities import CapabilityRegistry, CommandRejected
from gauntlet.config import Settings
from gauntlet.instruments import MockCamera, detect_instruments, imaging, is_simulated, v4l2
from gauntlet.instruments.imaging import ImageError, encode_frame, encode_png, image_suffix, measure, yuyv_to_rgb
from gauntlet.instruments.uvc_camera import UvcCamera
from gauntlet.instruments.v4l2 import PIXELFORMAT_MJPG, PIXELFORMAT_YUYV, Frame, V4l2Error, fourcc

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


def camera_with(fake: _FakeCamera, **kwargs: Any) -> UvcCamera:
    """A driver wired to one stand-in device."""
    return UvcCamera(device="/dev/video0", open_camera=lambda path: fake, **kwargs)


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
    def test_available_once_the_device_opens(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        assert camera.available() is True
        assert camera.describe()["unavailable_reason"] == ""

    def test_opening_starts_the_stream(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake)
        camera.available()
        assert fake.started is True

    def test_a_device_that_will_not_open_is_unavailable(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.open_error = V4l2Error("/dev/video0: device busy")
        camera = camera_with(fake)
        assert camera.available() is False
        assert "device busy" in camera.describe()["unavailable_reason"]

    def test_a_format_the_encoder_cannot_write_is_refused(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        fake.pixelformat = 0x3231564E
        camera = camera_with(fake)
        assert camera.available() is False
        assert "not supported" in camera.describe()["unavailable_reason"]

    def test_a_failed_probe_is_not_retried_until_the_interval_passes(self) -> None:
        clock = _Clock()
        fake = _FakeCamera(Path("/dev/video0"))
        fake.open_error = V4l2Error("/dev/video0: device has gone")
        opened: list[int] = []

        def build(path: Path) -> _FakeCamera:
            opened.append(1)
            return fake

        camera = UvcCamera(clock=clock, device="/dev/video0", open_camera=build, probe_interval_s=3.0)
        assert camera.available() is False
        assert camera.available() is False
        assert len(opened) == 1

        clock.advance(3.0)
        assert camera.available() is False
        assert len(opened) == 2

    def test_an_open_device_is_not_reprobed(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        opened: list[int] = []

        def build(path: Path) -> _FakeCamera:
            opened.append(1)
            return fake

        camera = UvcCamera(device="/dev/video0", open_camera=build)
        assert camera.available() is True
        assert camera.available() is True
        assert len(opened) == 1

    def test_snapshot_returns_an_encoded_image(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), warmup_frames=0)
        result = camera.command("snapshot", {})
        assert base64.b64decode(result["image_base64"]).startswith(_PNG_SIGNATURE)
        assert result["suffix"] == ".png"
        assert result["source"] == {"width": 8, "height": 8, "fourcc": "YUYV"}

    def test_snapshot_discards_the_frames_already_queued(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=2)
        result = camera.command("snapshot", {})
        # Three grabbed, and the last is the one reported.
        assert fake.frames_grabbed == 3
        assert result["sequence"] == 3

    def test_state_counts_snapshots_without_taking_one(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=0)
        camera.command("snapshot", {})
        grabbed = fake.frames_grabbed
        state = camera.state()
        assert state["snapshots"] == 1
        assert state["streaming"] is True
        assert fake.frames_grabbed == grabbed

    def test_a_camera_that_stops_answering_is_dropped(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake, warmup_frames=0)
        camera.available()
        fake.grab_error = V4l2Error("/dev/video0: device has gone")
        with pytest.raises(CommandRejected, match="device has gone"):
            camera.command("snapshot", {})
        assert fake.closed is True

    def test_an_unknown_command_is_rejected(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        with pytest.raises(CommandRejected, match="no command"):
            camera.command("record", {})

    def test_a_width_out_of_range_is_rejected(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        with pytest.raises(CommandRejected, match="between"):
            camera.command("snapshot", {"max_width": 4})

    def test_a_width_that_is_not_a_number_is_rejected(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")))
        with pytest.raises(CommandRejected, match="must be a number"):
            camera.command("snapshot", {"max_width": "wide"})

    def test_a_missing_width_takes_the_default(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), warmup_frames=0)
        assert camera.command("snapshot", {})["width"] == 8

    def test_write_runs_a_command_and_returns_the_image(self) -> None:
        camera = camera_with(_FakeCamera(Path("/dev/video0")), warmup_frames=0)
        result = camera.write({"command": "snapshot", "args": {}})
        assert "image_base64" in result

    def test_close_releases_the_device(self) -> None:
        fake = _FakeCamera(Path("/dev/video0"))
        camera = camera_with(fake)
        camera.available()
        camera.close()
        assert fake.closed is True


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
