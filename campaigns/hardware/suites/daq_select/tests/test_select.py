"""Which units a run picks, and what a capture across them is judged against."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from gauntlet_sdk import IterationOutcome
from pydantic import ValidationError
from suite import daq
from suite.daq import DaqError
from suite.profile import DaqSelectProfile, selected_keys
from suite.runner import _evaluate


def _instruments(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"instruments": list(rows)}


def _row(name: str, instance: str, *, kind: str = "daq", available: bool = True, reason: str = "") -> dict[str, Any]:
    return {
        "name": name,
        "kind": kind,
        "instance_id": instance,
        "available": available,
        "unavailable_reason": reason,
    }


@pytest.fixture
def bench(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """A bench holding two DAQs and a supply, served in place of Gauntlet's API."""
    rows = [_row("daq.0", "daq0"), _row("daq.1", "daq1"), _row("psu", "psu0", kind="psu")]
    monkeypatch.setattr(daq, "_get", lambda url, timeout_s: _instruments(*rows))
    return rows


def outcome(**units: dict[str, float]) -> IterationOutcome:
    return IterationOutcome(success=True, reason="", metrics=units, phase_records=[], summary="")


class TestSelection:
    def test_naming_none_measures_every_daq_and_nothing_else(self, bench: list[dict[str, Any]]) -> None:
        units = daq.find_units("http://gauntlet/api", [])
        assert [(u.key, u.instance) for u in units] == [("daq.0", "daq0"), ("daq.1", "daq1")]

    def test_naming_one_measures_only_that_one(self, bench: list[dict[str, Any]]) -> None:
        units = daq.find_units("http://gauntlet/api", ["daq.1"])
        assert [u.key for u in units] == ["daq.1"]
        assert units[0].url == "http://gauntlet/api/capabilities/daq.1"

    def test_an_unregistered_name_is_refused_with_what_is_registered(self, bench: list[dict[str, Any]]) -> None:
        with pytest.raises(DaqError, match=r"no DAQ named daq\.7 \(registered: daq\.0, daq\.1\)"):
            daq.find_units("http://gauntlet/api", ["daq.7"])

    def test_a_supply_is_not_a_daq(self, bench: list[dict[str, Any]]) -> None:
        with pytest.raises(DaqError, match="no DAQ named psu"):
            daq.find_units("http://gauntlet/api", ["psu"])

    def test_a_unit_that_is_not_answering_is_refused_with_its_reason(self, bench: list[dict[str, Any]]) -> None:
        bench[1] = _row("daq.1", "daq1", available=False, reason="no DI-2008 on the USB bus")
        with pytest.raises(DaqError, match=r"daq\.1 not answering: no DI-2008 on the USB bus"):
            daq.find_units("http://gauntlet/api", [])

    def test_a_bench_with_no_daq_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(daq, "_get", lambda url, timeout_s: _instruments())
        with pytest.raises(DaqError, match="no DAQ registered"):
            daq.find_units("http://gauntlet/api", [])

    def test_no_api_is_refused(self) -> None:
        with pytest.raises(DaqError, match="no Gauntlet API"):
            daq.find_units(None, [])


class TestProfile:
    def test_the_selection_is_a_comma_separated_list(self) -> None:
        assert selected_keys("daq.0, daq.1,") == ["daq.0", "daq.1"]

    def test_an_empty_selection_names_no_unit(self) -> None:
        assert selected_keys("") == []

    def test_a_name_that_is_not_a_daq_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not an instrument name"):
            DaqSelectProfile(daqs="psu")

    def test_a_unit_named_twice_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="listed more than once"):
            DaqSelectProfile(daqs="daq.0,daq.0")


class TestEvaluate:
    def test_every_unit_reading_is_a_pass(self) -> None:
        profile = DaqSelectProfile()
        assert _evaluate([outcome(daq0={"ch_1": 1.0}, daq1={"ch_1": 2.0})], profile) == (True, "")

    def test_a_unit_that_never_answered_fails_the_run(self) -> None:
        profile = DaqSelectProfile(max_missed_samples=5)
        verdict = _evaluate([outcome(daq0={"ch_1": 1.0}, daq1={})], profile)
        assert verdict == (False, "no reading at all from daq1 ch_1")

    def test_a_missed_sample_counts_against_the_tolerance(self) -> None:
        profile = DaqSelectProfile()
        missed = IterationOutcome(
            success=False, reason="x", metrics={"daq0": {"ch_1": 1.0}}, phase_records=[], summary=""
        )
        ok, reason = _evaluate([missed], profile) or (True, "")
        assert not ok
        assert "1 of 1 samples missed" in reason


def test_the_mock_profile_shipped_with_the_suite_loads() -> None:
    import yaml

    text = (Path(__file__).resolve().parents[1] / "profiles" / "mock.yaml").read_text()
    profile = DaqSelectProfile.model_validate(yaml.safe_load(text))
    assert selected_keys(profile.daqs) == ["daq.0", "daq.1"]
