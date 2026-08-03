"""Per-phase timing and failure capture within one iteration."""

from __future__ import annotations

import pytest

from gauntlet_sdk import PhaseRecord, PhaseTimer


class TestPhaseTimer:
    def test_a_clean_phase_is_recorded_as_a_success(self):
        records: list[PhaseRecord] = []
        with PhaseTimer("boot", records):
            pass

        assert len(records) == 1
        assert records[0].name == "boot"
        assert records[0].success
        assert records[0].error is None
        assert records[0].elapsed_s >= 0.0

    def test_an_exception_is_recorded_and_re_raised(self):
        records: list[PhaseRecord] = []
        with pytest.raises(RuntimeError, match="serial port busy"), PhaseTimer("connect", records):
            raise RuntimeError("serial port busy")

        assert len(records) == 1
        assert not records[0].success
        assert records[0].error == "RuntimeError: serial port busy"

    def test_elapsed_is_recorded_on_failure_too(self):
        records: list[PhaseRecord] = []
        with pytest.raises(ValueError), PhaseTimer("measure", records):
            raise ValueError("out of range")

        assert records[0].elapsed_s >= 0.0

    def test_detail_set_inside_the_block_reaches_the_record(self):
        records: list[PhaseRecord] = []
        with PhaseTimer("boot", records) as phase:
            phase.set_detail(host="unit-3", attempt=2)

        assert records[0].detail == {"host": "unit-3", "attempt": "2"}

    def test_detail_survives_a_failing_phase(self):
        records: list[PhaseRecord] = []
        with pytest.raises(TimeoutError), PhaseTimer("boot", records) as phase:
            phase.set_detail(host="unit-3")
            raise TimeoutError("no response")

        assert records[0].detail == {"host": "unit-3"}

    def test_phases_append_to_the_sink_in_order(self):
        records: list[PhaseRecord] = []
        for name in ("connect", "measure", "disconnect"):
            with PhaseTimer(name, records):
                pass

        assert [record.name for record in records] == ["connect", "measure", "disconnect"]
