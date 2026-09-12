"""Reclaiming the camera when the driver has disowned it mid-run."""

from __future__ import annotations

import pytest
from suite.link import LinkError, Reading
from suite.runner import _read_link


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
