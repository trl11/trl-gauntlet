"""The frame parser, against the lines section 7.4 of the firmware documents."""

from __future__ import annotations

from pathlib import Path

from suite import frames
from suite.board import report_counters, streaming


def test_boot_frame_carries_its_cause_and_version() -> None:
    frame = frames.parse("@B,0,58,12,rst=WDT,bits=0x0010,fw=3.0.0-5aa8a60f")
    assert frame is not None
    assert (frame.kind, frame.seq, frame.ms, frame.boot) == ("B", 0, 58, 12)
    assert frame.fields["rst"] == "WDT"
    assert frame.fields["fw"] == "3.0.0-5aa8a60f"


def test_detection_frame_separates_its_code_from_its_fields() -> None:
    frame = frames.parse("@E,2,2110,12,SFRUPSET,reg=0x0F92,exp=0x003F,got=0x003B")
    assert frame is not None
    assert frame.code == "SFRUPSET"
    assert frames.CODE_TAGS[frame.code] == "sfr"
    assert frame.fields == {"reg": "0x0F92", "exp": "0x003F", "got": "0x003B"}


def test_summary_frame_yields_every_counter() -> None:
    frame = frames.parse("@S,61,60058,12,sfr=1,ref=0,sram=1,stuck=0,flash=0,cfg=0,lat=0,flt=0,test=0,drop=0")
    assert frame is not None
    assert frames.counters(frame) == {
        "cfg": 0,
        "drop": 0,
        "flash": 0,
        "flt": 0,
        "lat": 0,
        "ref": 0,
        "sfr": 1,
        "sram": 1,
        "stuck": 0,
        "test": 0,
    }


def test_human_text_is_not_a_frame() -> None:
    assert frames.parse("frames=on seq=1 ev=0 drop=0") is None
    assert frames.parse("HB1=0 HB2=0 Jetson_IS=1538") is None


def test_a_truncated_or_corrupted_frame_parses_to_nothing() -> None:
    # A line off a wire is itself something this run is looking for, so the
    # caller counts what did not parse rather than the parser raising.
    assert frames.parse("@T,1,1058") is None
    assert frames.parse("@T,1,x,12,ev=0") is None
    assert frames.parse("@") is None


def test_sequence_delta_crosses_the_sixteen_bit_wrap() -> None:
    assert frames.seq_delta(65534, 2) == 4
    assert frames.seq_delta(10, 11) == 1


def _beat(seq: int, ms: int, boot: int = 1) -> frames.Frame:
    return frames.Frame(kind="T", seq=seq, ms=ms, boot=boot)


def test_a_steady_beat_reads_as_healthy() -> None:
    assert frames.classify(_beat(1, 1000), _beat(2, 2158), 1158.0, 400.0) == "healthy"


def test_a_jumped_counter_is_told_from_a_lost_frame() -> None:
    # One beat period of elapsed time with the counter far ahead is the RAM
    # counter having been corrupted; no frame was lost.
    assert frames.classify(_beat(1, 1000), _beat(900, 2158), 1158.0, 400.0) == "seq_jumped"
    # Three periods with three frames' worth of counter is the link losing them.
    assert frames.classify(_beat(1, 1000), _beat(4, 4474), 1158.0, 400.0) == "frames_lost"


def test_a_stalled_clock_is_told_from_both() -> None:
    assert frames.classify(_beat(1, 1000), _beat(2, 1005), 1158.0, 400.0) == "clock_moved"


def test_a_console_report_reads_back_its_state_and_counters() -> None:
    line = "frames=on seq=61 ev=2 drop=0 SFRUPSET=1 SRAMUPSET=1"
    assert streaming(line)
    assert not streaming("frames=off seq=0 ev=0 drop=0")
    assert report_counters(line)["ev"] == 2
    assert report_counters(line)["SFRUPSET"] == 1


def test_a_version_reply_reads_back_as_fields() -> None:
    from suite import firmware

    assert firmware.identify("fw=3.0.0-19f0af71 api=6 build=production") == {
        "api": "6",
        "build": "production",
        "fw": "3.0.0-19f0af71",
    }


def test_the_image_reports_the_version_the_board_reports() -> None:

    from suite import firmware

    image = firmware.locate("auto", Path(__file__).resolve().parents[1])
    assert image is not None
    assert image.name == "pmu3_firmware.hex"
    assert firmware.image_version(image) == "3.0.0-19f0af71"


def test_an_image_that_is_not_intel_hex_reports_no_version(tmp_path: Path) -> None:
    from suite import firmware

    junk = tmp_path / "pmu3_firmware.hex"
    junk.write_text("not a hex file at all\n:00\n")
    assert firmware.image_version(junk) == ""
    assert firmware.image_version(tmp_path / "absent.hex") == ""


def test_the_committed_image_is_the_one_auto_finds() -> None:
    from pathlib import Path

    from suite import firmware

    suite_dir = Path(__file__).resolve().parents[1]
    image = firmware.locate("auto", suite_dir)
    assert image is not None
    # The suite's own copy wins over anything the submodule has built, so a
    # bench measures the image the tree says it does.
    assert image.parent == suite_dir / "firmware"
    assert firmware.digest(image)
