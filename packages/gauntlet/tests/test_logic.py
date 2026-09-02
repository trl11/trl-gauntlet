"""The logic analyzer: its protocol, its measurements, and its picture.

Every test here runs against a stand-in for the board, so none of them needs
hardware attached.
"""

from __future__ import annotations

import base64
from typing import Any

import pytest

from gauntlet.capabilities import CommandRejected
from gauntlet.instruments import waveform
from gauntlet.instruments.fx2_logic import (
    CMD_GET_FW_VERSION,
    CMD_START,
    CPUCS_ADDRESS,
    FIRMWARE_CHUNK,
    REQUEST_FIRMWARE,
    Fx2Logic,
    Fx2LogicError,
    firmware_file,
    firmware_name,
    read_size,
    read_timeout_ms,
    sample_delay,
    start_command,
    upload_firmware,
)
from gauntlet.instruments.mock_logic import MockLogic, pattern


class _Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeAnalyzer:
    """Enough of an FX2 board to answer what the driver asks it."""

    def __init__(
        self,
        *,
        burst: int = 0,
        loaded: bool = True,
        stale: bytes = b"",
        stale_forever: bool = False,
        stream: bytes = b"",
    ) -> None:
        self.burst = burst
        self.closed = False
        self.reads: list[tuple[int, int]] = []
        self.started = False
        self.stale = stale
        self.stale_forever = stale_forever
        self.stream = stream
        self.writes: list[tuple[int, int, bytes]] = []
        self._loaded = loaded

    def close(self) -> None:
        self.closed = True

    def control_in(self, request: int, size: int) -> bytes:
        return bytes([1, 3]) if request == CMD_GET_FW_VERSION else b""

    def control_out(self, request: int, value: int, payload: bytes) -> None:
        self.writes.append((request, value, payload))
        if request == CMD_START:
            self.started = True

    def identity(self) -> dict[str, str]:
        return {
            "manufacturer": "sigrok" if self._loaded else "",
            "model": "Saleae Logic",
            "product": "fx2lafw" if self._loaded else "",
            "product_id": "3881",
            "serial": "A1",
            "vendor_id": "0925",
        }

    def loaded(self) -> bool:
        return self._loaded

    def read(self, size: int, timeout_ms: int) -> bytes:
        """Whatever is left in the FIFO, which is stale until a capture starts.

        A real board sends at most one FIFO before it overruns and goes quiet,
        so ``burst`` caps what any one read hands back.
        """
        self.reads.append((size, timeout_ms))
        if not self.started:
            if self.stale_forever:
                return b"\xff" * size
            block, self.stale = self.stale[:size], self.stale[size:]
            return block
        taken = min(size, self.burst) if self.burst else size
        block, self.stream = self.stream[:taken], self.stream[taken:]
        return block


def _analyzer(transport: Any, clock: _Clock | None = None, **kwargs: Any) -> Fx2Logic:
    """A driver wired to a stand-in board rather than to the USB bus."""
    return Fx2Logic(clock=clock or _Clock(), open_transport=lambda _serial: transport, **kwargs)


class TestWaveform:
    def test_a_channel_is_its_bit_out_of_every_sample(self) -> None:
        samples = bytes([0b0000_0001, 0b0000_0000, 0b1000_0001])
        assert waveform.channel_column(samples, 0) == b"\x01\x00\x01"
        assert waveform.channel_column(samples, 7) == b"\x00\x00\x01"

    def test_a_channel_outside_the_byte_is_refused(self) -> None:
        with pytest.raises(ValueError):
            waveform.channel_column(b"\x00", waveform.CHANNEL_COUNT)

    def test_edges_are_the_changes_between_samples(self) -> None:
        assert waveform.count_edges(b"\x00\x01\x01\x00") == 2

    def test_the_first_sample_is_no_edge(self) -> None:
        # It has nothing before it to differ from, so a capture that opens
        # high is not an edge at sample zero.
        assert waveform.count_edges(b"\x01\x01\x01") == 0
        assert waveform.count_edges(b"\x01") == 0

    def test_a_square_wave_measures_its_own_frequency(self) -> None:
        # Ten samples a cycle at 1 kHz is 100 Hz, half of the edge rate.
        column = bytes(([1] * 5 + [0] * 5) * 10)
        assert waveform.measure(column, 1000.0) == {
            "duty": 50.0,
            "edges": 19,
            "frequency": 95.0,
            "level": 0,
        }

    def test_a_line_that_never_moves_reads_as_its_level(self) -> None:
        assert waveform.measure(b"\x01" * 100, 1000.0) == {
            "duty": 100.0,
            "edges": 0,
            "frequency": 0.0,
            "level": 1,
        }

    def test_an_empty_capture_measures_nothing(self) -> None:
        assert waveform.measure(b"", 1000.0)["edges"] == 0

    def test_a_capture_is_drawn_as_a_png(self) -> None:
        drawn = waveform.render(pattern(4096))
        assert drawn.startswith(b"\x89PNG\r\n\x1a\n")

    def test_a_capture_shorter_than_the_plot_is_still_drawn(self) -> None:
        assert waveform.render(b"\x01\x00").startswith(b"\x89PNG\r\n\x1a\n")

    def test_nothing_captured_is_still_drawn(self) -> None:
        # A board that answered with no samples leaves an empty plot rather
        # than an error out of the renderer.
        assert waveform.render(b"").startswith(b"\x89PNG\r\n\x1a\n")


class TestSampleRates:
    def test_a_rate_dividing_the_48_mhz_clock_uses_it(self) -> None:
        flags, delay = sample_delay(24_000_000)
        assert (flags, delay) == (0x40, 1)

    def test_a_delay_too_long_for_48_mhz_falls_back_to_30(self) -> None:
        # 48 MHz would need 2399 ticks, which is more than the six delay
        # states hold, so the slowest rates are only reachable at 30 MHz.
        flags, delay = sample_delay(20_000)
        assert (flags, delay) == (0x00, 1499)

    def test_a_rate_dividing_neither_clock_is_refused(self) -> None:
        with pytest.raises(ValueError):
            sample_delay(7_000_000)

    def test_a_rate_of_nothing_is_refused(self) -> None:
        with pytest.raises(ValueError):
            sample_delay(0)

    def test_the_start_command_carries_the_flags_then_the_delay(self) -> None:
        assert start_command(1_000_000) == bytes([0x40, 0x00, 47])


class TestFirmware:
    def test_the_image_is_the_one_the_board_s_ids_name(self) -> None:
        assert firmware_name(0x0925, 0x3881) == "fx2lafw-saleae-logic.fw"

    def test_a_board_nothing_knows_names_no_image(self) -> None:
        assert firmware_name(0x1234, 0x5678) == ""

    def test_a_directory_is_searched_for_the_image(self, tmp_path: Any) -> None:
        (tmp_path / "fx2lafw-saleae-logic.fw").write_bytes(b"\x00")
        found = firmware_file(str(tmp_path), "fx2lafw-saleae-logic.fw")
        assert found == tmp_path / "fx2lafw-saleae-logic.fw"

    def test_a_file_is_taken_whatever_it_is_called(self, tmp_path: Any) -> None:
        image = tmp_path / "analyzer.fw"
        image.write_bytes(b"\x00")
        assert firmware_file(str(image), "fx2lafw-saleae-logic.fw") == image

    def test_an_image_that_is_nowhere_is_nothing(self, tmp_path: Any) -> None:
        assert firmware_file(str(tmp_path), "fx2lafw-saleae-logic.fw") is None

    def test_the_core_is_held_in_reset_for_the_whole_write(self) -> None:
        board = _FakeAnalyzer(loaded=False)
        upload_firmware(board, b"\xaa" * (FIRMWARE_CHUNK + 16))
        assert board.writes[0] == (REQUEST_FIRMWARE, CPUCS_ADDRESS, b"\x01")
        assert board.writes[-1] == (REQUEST_FIRMWARE, CPUCS_ADDRESS, b"\x00")

    def test_the_image_is_written_from_address_zero_in_chunks(self) -> None:
        board = _FakeAnalyzer(loaded=False)
        upload_firmware(board, b"\xaa" * (FIRMWARE_CHUNK + 16))
        chunks = [(value, len(payload)) for _request, value, payload in board.writes[1:-1]]
        assert chunks == [(0, FIRMWARE_CHUNK), (FIRMWARE_CHUNK, 16)]


class TestReads:
    def test_a_read_asks_for_whole_endpoint_packets(self) -> None:
        # A bulk read is delivered a packet at a time, so a part of one is
        # asking for a packet that will not fit.
        assert read_size(2000) == 2048
        assert read_size(512) == 512

    def test_a_read_never_asks_for_more_than_the_board_s_fifo(self) -> None:
        assert read_size(10_000_000) == 16384

    def test_a_read_waits_as_long_as_the_samples_it_asked_for_take(self) -> None:
        # 2048 samples at 20 kHz take 102ms, so a 100ms wait would time out
        # and lose them: pyusb hands back nothing when a transfer times out.
        assert read_timeout_ms(2048, 20_000) > 150

    def test_a_fast_rate_keeps_the_floor(self) -> None:
        assert read_timeout_ms(16384, 24_000_000) == 100


class TestFx2Logic:
    def test_a_board_without_firmware_is_loaded_and_not_yet_available(self, tmp_path: Any) -> None:
        (tmp_path / "fx2lafw-saleae-logic.fw").write_bytes(b"\xaa" * 32)
        board = _FakeAnalyzer(loaded=False)
        analyzer = _analyzer(board, firmware=str(tmp_path))
        assert analyzer.available() is False
        assert board.writes[0][0] == REQUEST_FIRMWARE
        assert "waiting for the analyzer" in analyzer.describe()["unavailable_reason"]
        # The board renumerates, so the transport it was loaded through is
        # finished with rather than kept.
        assert board.closed is True

    def test_a_board_waiting_for_its_firmware_is_still_attached(self, tmp_path: Any) -> None:
        (tmp_path / "fx2lafw-saleae-logic.fw").write_bytes(b"\xaa" * 32)
        analyzer = _analyzer(_FakeAnalyzer(loaded=False), firmware=str(tmp_path))
        # Registration turns on this: an operator sees the board and the
        # reason it cannot be used yet, rather than an empty page.
        assert analyzer.attached() is True

    def test_a_missing_image_names_the_file_and_the_package(self, tmp_path: Any) -> None:
        analyzer = _analyzer(_FakeAnalyzer(loaded=False), firmware=str(tmp_path))
        assert analyzer.available() is False
        reason = analyzer.describe()["unavailable_reason"]
        assert "fx2lafw-saleae-logic.fw" in reason
        assert "sigrok-firmware-fx2lafw" in reason

    def test_firmware_is_written_once_however_often_it_is_polled(self, tmp_path: Any) -> None:
        (tmp_path / "fx2lafw-saleae-logic.fw").write_bytes(b"\xaa" * 32)
        board = _FakeAnalyzer(loaded=False)
        analyzer = _analyzer(board, firmware=str(tmp_path))
        for _ in range(5):
            analyzer.available()
        assert sum(1 for request, value, _ in board.writes if value == CPUCS_ADDRESS) == 2

    def test_a_loaded_board_reports_the_firmware_it_is_running(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        assert analyzer.available() is True
        assert analyzer.describe()["firmware"] == "1.3"
        assert analyzer.connection() == "USB 0925:3881 serial A1"

    def test_nothing_on_the_bus_is_unavailable_rather_than_an_error(self) -> None:
        def nothing(_serial: str) -> Any:
            raise Fx2LogicError("no logic analyzer on the USB bus")

        analyzer = Fx2Logic(clock=_Clock(), open_transport=nothing)
        assert analyzer.available() is False
        assert analyzer.attached() is False
        assert analyzer.describe()["unavailable_reason"] == "no logic analyzer on the USB bus"

    def test_a_capture_measures_every_channel(self) -> None:
        board = _FakeAnalyzer(stream=pattern(2000))
        analyzer = _analyzer(board)
        result = analyzer.command("capture", {"rate": "1mhz", "window": "1ms"})
        assert result["samples"] == 1000
        assert result["rate_hz"] == 1_000_000
        assert sorted(result["channels"]) == [str(number) for number in range(1, 9)]
        # Channel 1 turns over every four samples, which at 1 MHz is 125 kHz.
        assert result["channels"]["1"]["frequency"] == pytest.approx(125_000, rel=0.01)

    def test_a_capture_answers_with_a_picture_of_itself(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer(stream=pattern(2000)))
        result = analyzer.command("capture", {"rate": "1mhz", "window": "1ms"})
        assert result["suffix"] == ".png"
        assert base64.b64decode(result["image_base64"]).startswith(b"\x89PNG\r\n\x1a\n")

    def test_a_suite_drives_it_through_the_capability_endpoint(self) -> None:
        # `POST /api/capabilities/logic` reaches `write`, which is how a suite
        # drives the analyzer: it never calls `command` itself.
        analyzer = _analyzer(_FakeAnalyzer(stream=pattern(2000)))
        result = analyzer.write({"command": "capture", "args": {"rate": "1mhz", "window": "1ms"}})
        assert result["samples"] == 1000
        assert "image_base64" in result

    def test_a_command_that_settles_something_answers_with_the_state(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        state = analyzer.write({"command": "configure", "args": {"rows": {"1": {"label": "SCL"}}}})
        assert state["channels"]["1"]["label"] == "SCL"

    def test_the_capture_command_is_what_the_panel_draws(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        capture = next(row for row in analyzer.commands() if row["name"] == "capture")
        assert analyzer.primary_command() == "capture"
        assert capture["returns"] == "image"

    def test_stale_samples_are_drained_before_a_capture(self) -> None:
        # The firmware never stops sampling, so whatever the last capture left
        # behind would otherwise be read as the start of this one.
        board = _FakeAnalyzer(stale=b"\xff" * 64, stream=pattern(2000))
        analyzer = _analyzer(board)
        result = analyzer.command("capture", {"rate": "1mhz", "window": "1ms"})
        assert board.stale == b""
        assert result["channels"]["1"]["duty"] == 50.0

    def test_a_board_that_never_goes_quiet_is_drained_only_so_far(self) -> None:
        # Reading is what lets the firmware keep sampling, so a board left
        # streaming answers every read. An unbounded drain would never reach
        # the capture it is draining for.
        board = _FakeAnalyzer(stale_forever=True, stream=pattern(2000))
        result = _analyzer(board).command("capture", {"rate": "1mhz", "window": "1ms"})
        assert result["samples"] == 1000

    def test_the_capture_starts_the_board_at_the_rate_asked_for(self) -> None:
        board = _FakeAnalyzer(stream=pattern(2000))
        _analyzer(board).command("capture", {"rate": "2mhz", "window": "1ms"})
        assert (CMD_START, 0, start_command(2_000_000)) in board.writes

    def test_a_board_that_overruns_reports_the_window_it_gave(self) -> None:
        # Measured on a bench board: at the fastest rates one FIFO arrives and
        # the board goes quiet. That is a short capture, not a failed one.
        board = _FakeAnalyzer(burst=16384, stream=pattern(16384))
        result = _analyzer(board).command("capture", {"rate": "24mhz", "window": "10ms"})
        assert result["samples"] == 16384
        assert result["window_s"] == round(16384 / 24_000_000, 6)

    def test_a_board_that_sends_nothing_is_refused_rather_than_measured(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer(stream=b""))
        with pytest.raises(CommandRejected, match="no samples"):
            analyzer.command("capture", {"rate": "1mhz", "window": "1ms"})

    def test_an_unknown_rate_is_refused(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer(stream=pattern(100)))
        with pytest.raises(CommandRejected, match="rate"):
            analyzer.command("capture", {"rate": "99mhz", "window": "1ms"})

    def test_an_unknown_window_is_refused(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer(stream=pattern(100)))
        with pytest.raises(CommandRejected, match="window"):
            analyzer.command("capture", {"rate": "1mhz", "window": "1s"})

    def test_an_unknown_command_is_refused(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        with pytest.raises(CommandRejected, match="no command"):
            analyzer.command("trigger", {})

    def test_a_channel_reads_as_its_number_until_it_is_named(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        assert analyzer.state()["channels"]["3"]["label"] == "CH 3"

    def test_naming_a_channel_renames_its_reading_everywhere(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        analyzer.command("configure", {"rows": {"3": {"label": "SCL"}}})
        assert analyzer.state()["channels"]["3"]["label"] == "SCL"
        assert [entry["label"] for entry in analyzer.readouts() if entry["key"] == "channels.3.level"] == ["SCL"]

    def test_a_label_is_trimmed_to_one_line_of_ordinary_spacing(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        analyzer.command("configure", {"rows": {"1": {"label": "  reset \n line "}}})
        assert analyzer.state()["channels"]["1"]["label"] == "reset line"

    def test_an_unknown_channel_is_refused(self) -> None:
        analyzer = _analyzer(_FakeAnalyzer())
        with pytest.raises(CommandRejected, match="no channel"):
            analyzer.command("configure", {"rows": {"9": {"label": "SDA"}}})

    def test_naming_a_channel_needs_no_board(self) -> None:
        # Labels are the panel's, not the board's, so an analyzer waiting for
        # its firmware can still be labelled up.
        def nothing(_serial: str) -> Any:
            raise Fx2LogicError("no logic analyzer on the USB bus")

        analyzer = Fx2Logic(clock=_Clock(), open_transport=nothing)
        analyzer.command("configure", {"rows": {"1": {"label": "SDA"}}})
        assert analyzer.state()["channels"]["1"]["label"] == "SDA"

    def test_a_capture_without_a_board_is_refused(self) -> None:
        def nothing(_serial: str) -> Any:
            raise Fx2LogicError("no logic analyzer on the USB bus")

        analyzer = Fx2Logic(clock=_Clock(), open_transport=nothing)
        with pytest.raises(CommandRejected, match="unavailable"):
            analyzer.command("capture", {})

    def test_closing_releases_the_board(self) -> None:
        board = _FakeAnalyzer()
        analyzer = _analyzer(board)
        analyzer.available()
        analyzer.close()
        assert board.closed is True

    def test_a_board_that_stops_answering_is_reprobed_on_an_interval(self) -> None:
        clock = _Clock()
        board = _FakeAnalyzer()
        opened = []

        def open_once(_serial: str) -> Any:
            opened.append(clock.now)
            raise Fx2LogicError("no logic analyzer on the USB bus")

        analyzer = Fx2Logic(clock=clock, open_transport=open_once, probe_interval_s=3.0)
        assert analyzer.available() is False
        analyzer.available()
        assert len(opened) == 1
        clock.advance(4.0)
        analyzer.available()
        assert len(opened) == 2
        assert board.closed is False


class TestMockLogic:
    def test_the_pattern_repeats_rather_than_being_computed_per_sample(self) -> None:
        block = pattern(4096)
        assert block[:1024] == block[1024:2048]

    def test_every_channel_halves_the_one_above_it(self) -> None:
        analyzer = MockLogic()
        analyzer.command("capture", {"rate": "1mhz", "window": "1ms"})
        channels = analyzer.state()["channels"]
        assert channels["2"]["frequency"] == pytest.approx(channels["1"]["frequency"] / 2, rel=0.05)

    def test_a_capture_answers_with_a_picture_of_itself(self) -> None:
        result = MockLogic().command("capture", {"rate": "1mhz", "window": "1ms"})
        assert base64.b64decode(result["image_base64"]).startswith(b"\x89PNG\r\n\x1a\n")

    def test_captures_are_counted_for_the_viewer(self) -> None:
        analyzer = MockLogic()
        analyzer.command("capture", {})
        analyzer.command("capture", {})
        assert analyzer.state()["captures"] == 2

    def test_it_declares_what_the_real_analyzer_declares(self) -> None:
        assert [row["name"] for row in MockLogic().commands()] == [
            row["name"] for row in _analyzer(_FakeAnalyzer()).commands()
        ]

    def test_naming_a_channel_renames_its_reading(self) -> None:
        analyzer = MockLogic()
        analyzer.command("configure", {"rows": {"8": {"label": "IRQ"}}})
        assert analyzer.state()["channels"]["8"]["label"] == "IRQ"

    def test_an_unknown_command_is_refused(self) -> None:
        with pytest.raises(CommandRejected, match="no command"):
            MockLogic().command("trigger", {})

    def test_a_suite_drives_it_through_the_capability_endpoint(self) -> None:
        result = MockLogic().write({"command": "capture", "args": {"rate": "1mhz", "window": "1ms"}})
        assert "image_base64" in result

    def test_it_is_a_simulation(self) -> None:
        assert MockLogic().describe()["driver"] == "mock"
