"""The accounting a tick does over the lines it drained."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from suite import runner


@dataclass
class _Recorder:
    """Stands in for the anomaly log, so a tick can be accounted without a run."""

    entries: list[tuple[str, str, dict[str, Any]]] = field(default_factory=list)

    def record(
        self, probe: str, kind: str, *, iteration: int | None = None, detail: dict[str, Any] | None = None
    ) -> None:
        self.entries.append((probe, kind, dict(detail or {})))

    def kinds(self) -> list[str]:
        return [kind for _, kind, _ in self.entries]


@dataclass
class _Ctx:
    extras: dict[str, Any]


@dataclass
class _Ictx:
    iteration: int = 1


def _context() -> tuple[_Ctx, _Recorder]:
    recorder = _Recorder()
    return _Ctx(
        extras={
            runner._ANOMALIES: recorder,
            runner._BOOT: None,
            runner._FIRMWARE: {},
            runner._IMAGE: {},
            runner._HOST_ORIGIN: None,
            runner._CLOCK_PAIRS: [],
            runner._LAST_BEAT: None,
            runner._LAST_SEEN: 0.0,
            runner._ORIGIN: None,
            runner._RESETS: 0,
            runner._TOTALS: {},
            runner._UNPARSED: 0,
        }
    ), recorder


def test_beats_are_counted_and_the_firmware_version_is_kept() -> None:
    ctx, _ = _context()
    beats = runner._account(
        ctx,
        _Ictx(),
        [
            "@B,0,58,12,rst=POR,bits=0x0001,fw=3.0.0-5aa8a60f",
            "@T,1,1058,12,ev=0,drop=0",
            "@T,2,2216,12,ev=0,drop=0",
        ],
    )
    assert beats == 2
    # A boot frame carrying a build updates the identity read off the console
    # at setup, which is how a mid-run reboot is noticed.
    assert ctx.extras[runner._FIRMWARE]["fw"] == "3.0.0-5aa8a60f"
    assert ctx.extras[runner._LAST_BEAT].seq == 2


def test_a_detection_is_counted_and_never_fails_the_tick() -> None:
    ctx, recorder = _context()
    runner._account(
        ctx,
        _Ictx(),
        [
            "@T,1,1058,12,ev=0,drop=0",
            "@E,2,2110,12,SFRUPSET,reg=0x0F92,exp=0x003F,got=0x003B",
            "@E,3,2110,12,SRAMUPSET,addr=0x0412",
        ],
    )
    assert ctx.extras[runner._TOTALS] == {"sfr": 1, "sram": 1}
    assert recorder.kinds() == ["SFRUPSET", "SRAMUPSET"]


def test_a_summary_frame_replaces_the_running_totals() -> None:
    # '@S' is the complete record when the rate limit dropped an '@E', so it
    # overwrites rather than adds to what was counted line by line.
    ctx, _ = _context()
    runner._account(ctx, _Ictx(), ["@E,1,10,12,SFRUPSET,reg=0x0F92"])
    runner._account(ctx, _Ictx(), ["@S,61,60058,12,sfr=9,sram=2,drop=3"])
    assert ctx.extras[runner._TOTALS]["sfr"] == 9
    assert ctx.extras[runner._TOTALS]["drop"] == 3


def test_a_moved_boot_count_is_a_reset_and_restarts_the_clock_baseline() -> None:
    ctx, recorder = _context()
    runner._account(ctx, _Ictx(), ["@T,1,1058,12,ev=0,drop=0"])
    assert ctx.extras[runner._ORIGIN] == 1058
    runner._account(ctx, _Ictx(), ["@B,0,58,13,rst=WDT,bits=0x0010,fw=3.0.0"])
    assert ctx.extras[runner._RESETS] == 1
    # Both clocks restart with the part, so a drift measured across the reboot
    # would be the reboot rather than the oscillator.
    assert ctx.extras[runner._ORIGIN] is None
    assert "reset" in recorder.kinds()


def test_a_line_that_did_not_parse_is_counted_rather_than_dropped() -> None:
    ctx, recorder = _context()
    runner._account(ctx, _Ictx(), ["@T,1,x,12,ev=0", "HB1=0 HB2=0 Jetson_IS=1538"])
    assert ctx.extras[runner._UNPARSED] == 1
    assert recorder.kinds() == ["unparsed"]
