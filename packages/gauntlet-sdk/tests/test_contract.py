"""The contract models are the definition, so their edges get tested directly."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from gauntlet_sdk.contract import (
    CONTRACT_MODELS,
    MetricsRecord,
    SuiteManifest,
    Verdict,
    json_schema,
)


def _manifest(**overrides):
    base = {
        "apiVersion": 1,
        "key": "demo",
        "title": "Demo",
        "exec": {"command": ["python", "-m", "demo"]},
    }
    base.update(overrides)
    return base


class TestSuiteManifest:
    def test_minimal_manifest_is_valid(self):
        manifest = SuiteManifest.model_validate(_manifest())
        assert manifest.key == "demo"
        assert manifest.produces == ["verdict"]
        assert manifest.supports.target is True

    def test_unknown_key_is_rejected(self):
        with pytest.raises(ValidationError, match="typoed_field"):
            SuiteManifest.model_validate(_manifest(typoed_field=1))

    @pytest.mark.parametrize("key", ["Demo", "1demo", "de-mo", "", "de mo"])
    def test_key_must_be_lower_snake_case(self, key):
        with pytest.raises(ValidationError):
            SuiteManifest.model_validate(_manifest(key=key))

    def test_empty_command_is_rejected(self):
        with pytest.raises(ValidationError):
            SuiteManifest.model_validate(_manifest(exec={"command": []}))

    def test_override_lookup(self):
        manifest = SuiteManifest.model_validate(
            _manifest(overrides=[{"name": "duration_s", "flag": "--duration-s", "type": "number"}])
        )
        assert manifest.override("duration_s").flag == "--duration-s"
        assert manifest.override("nope") is None

    def test_override_flag_must_look_like_a_flag(self):
        with pytest.raises(ValidationError):
            SuiteManifest.model_validate(_manifest(overrides=[{"name": "x", "flag": "-x"}]))


class TestVerdict:
    def test_failing_verdict_needs_a_reason(self):
        assert Verdict(passed=False, reason="rail out of tolerance").problems() == []
        problems = Verdict(passed=False).problems()
        assert len(problems) == 1
        assert "reason" in problems[0]

    def test_passing_verdict_needs_no_reason(self):
        assert Verdict(passed=True).problems() == []

    def test_unknown_fields_are_preserved(self):
        verdict = Verdict.model_validate({"passed": True, "suite_specific": {"a": 1}})
        assert verdict.model_dump()["suite_specific"] == {"a": 1}


class TestMetricsRecord:
    def test_iteration_record_requires_iteration_and_success(self):
        record = MetricsRecord.model_validate({"timestamp": 1.0, "iteration": 1, "success": True})
        assert record.problems() == []

    def test_iteration_record_without_success_is_flagged(self):
        record = MetricsRecord.model_validate({"timestamp": 1.0, "iteration": 1})
        assert any("success" in p for p in record.problems())

    def test_iteration_record_without_an_iteration_number_is_flagged(self):
        record = MetricsRecord.model_validate({"timestamp": 1.0, "success": True})
        assert any("`iteration` is required" in p for p in record.problems())

    def test_live_record_needs_neither(self):
        record = MetricsRecord.model_validate({"kind": "live", "timestamp": 1.0, "metrics": {"v": 1}})
        assert record.problems() == []

    def test_anomaly_record_requires_a_probe(self):
        assert MetricsRecord.model_validate({"kind": "anomaly", "timestamp": 1.0, "probe": "ssd"}).problems() == []
        assert MetricsRecord.model_validate({"kind": "anomaly", "timestamp": 1.0}).problems()

    def test_kind_defaults_to_iteration(self):
        assert MetricsRecord.model_validate({"timestamp": 1.0, "iteration": 2, "success": False}).kind == "iteration"


class TestSchemaGeneration:
    @pytest.mark.parametrize("name", sorted(CONTRACT_MODELS))
    def test_every_model_generates_a_schema(self, name):
        schema = json_schema(name)
        assert schema["type"] == "object"
        assert "properties" in schema

    def test_unknown_name_raises_with_the_known_ones(self):
        with pytest.raises(LookupError, match="verdict"):
            json_schema("nope")
