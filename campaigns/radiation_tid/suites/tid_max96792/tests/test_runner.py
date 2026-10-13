"""Reclaiming the camera when the driver has disowned it mid-run."""

from __future__ import annotations

import pytest
from gauntlet_sdk import IterationOutcome
from suite.link import LinkError, Reading
from suite.profile import TidMax96792Profile
from suite.runner import _evaluate, _eye_metrics, _eye_text, _read_link, _retrying, _summary


class _FakeCamera:
    """A camera that answers only while ``owned`` is true, like the real driver."""

    def __init__(self, *, owned: bool = True) -> None:
        self.owned = owned
        self.own_calls = 0
        self.reads = 0

    def link_status(self) -> Reading:
        self.reads += 1
        if not self.owned:
            raise LinkError("link_status: camera is not owned: own it before driving it")
        return Reading(chips={"0xb6": {"locked": True}})

    def own(self) -> None:
        self.own_calls += 1
        self.owned = True


class TestReadLink:
    def test_a_healthy_link_is_read_once(self) -> None:
        camera = _FakeCamera()
        reading = _read_link(camera)  # type: ignore[arg-type]
        assert reading.chips
        assert camera.reads == 1
        assert camera.own_calls == 0

    def test_a_dropped_camera_is_reclaimed_and_read_again(self) -> None:
        camera = _FakeCamera(owned=False)
        reading = _read_link(camera)  # type: ignore[arg-type]
        assert reading.chips
        assert camera.reads == 2
        assert camera.own_calls == 1

    def test_a_failure_that_is_not_about_ownership_is_not_retried(self) -> None:
        class _Broken:
            def link_status(self) -> Reading:
                raise LinkError("link_status: timed out")

            def own(self) -> None:
                raise AssertionError("must not try to reclaim a link that is merely unreadable")

        with pytest.raises(LinkError, match="timed out"):
            _read_link(_Broken())  # type: ignore[arg-type]

    def test_a_camera_that_will_not_come_back_still_raises(self) -> None:
        class _Gone:
            def link_status(self) -> Reading:
                raise LinkError("link_status: camera is not owned: own it before driving it")

            def own(self) -> None:
                raise LinkError("set_owned: camera is unavailable: no /dev/video* node is present")

        with pytest.raises(LinkError, match="unavailable"):
            _read_link(_Gone())  # type: ignore[arg-type]


def test_the_eye_opening_is_recorded_and_logged_only_where_a_chip_measured_it() -> None:
    deserializer = {"eye_a_pct": 75.0, "eye_b_pct": None, "locked": True}
    assert _eye_metrics(deserializer) == {"eye_a_pct": 75.0}
    assert _eye_text(deserializer) == " eye_a=75.0%"
    assert _eye_metrics({"locked": True}) == {}


def test_the_log_line_names_each_ends_decode_errors_and_eye() -> None:
    part = {"decode_errors_a": 3, "decode_errors_b": 0, "idle_errors": 1, "locked": True}
    far = {"0x50": {"decode_errors_a": 34, "decode_errors_b": 0, "eye_a_pct": 18.8, "idle_errors": 0, "locked": True}}
    line = _summary(part, far, {"mbps": 2574.0})
    assert "decode_a=3 decode_b=0 idle=1" in line
    assert "decode_a=34 decode_b=0 idle=0 eye_a=18.8%" in line
    assert line.startswith("locked ") and line.endswith(" 2574Mbps")


def test_a_failed_camera_call_is_tried_once_more() -> None:
    calls: list[int] = []

    def flaky() -> str:
        calls.append(1)
        if len(calls) == 1:
            raise LinkError("link_status: timed out")
        return "read"

    retries = [0]
    assert _retrying(flaky, retries) == "read"
    assert len(calls) == 2
    assert retries == [1]


def _sample(*, stalled: bool) -> IterationOutcome:
    metrics = {"bench": {"stalled": 1 if stalled else 0}}
    if not stalled:
        metrics["deserializer"] = {"answered": 1, "errors_total": 0, "locked": 1}
        metrics["video"] = {"arrived": 1}
    return IterationOutcome(success=True, reason="", metrics=metrics, phase_records=[], summary="")


def test_a_few_stalls_pass_and_too_many_fail_the_run() -> None:
    profile = TidMax96792Profile(max_stalled_pct=5)
    few = [_sample(stalled=index < 5) for index in range(100)]
    many = [_sample(stalled=index < 6) for index in range(100)]
    assert _evaluate(few, profile) is None
    verdict = _evaluate(many, profile)
    assert verdict is not None and verdict[0] is False and "stalled on 6 of 100" in verdict[1]
