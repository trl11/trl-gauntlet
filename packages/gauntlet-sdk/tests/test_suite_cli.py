"""The command line make_suite_cli builds, and the log helpers it prints through."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ConfigDict

from gauntlet_sdk import IterationContext, IterationOutcome, SuiteSpec, err, info, make_suite_cli, warn
from gauntlet_sdk.cli import _coerce


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    iterations: int = 1
    sample_period_s: float = 0.0
    duration_s: float = 0.0
    threshold_c: float = 80.0
    label: str = ""


def _iterate(ctx, ictx: IterationContext) -> IterationOutcome:
    return IterationOutcome(success=ctx.profile.threshold_c > 0, metrics={"n": ictx.iteration})


def _main(**kwargs):
    spec = SuiteSpec(
        name="demo",
        profile_model=Profile,
        iterate=_iterate,
        iteration_count=lambda p: p.iterations,
        sample_period_seconds=lambda p: p.sample_period_s,
    )
    return make_suite_cli(spec, **kwargs)


def _verdict(run_dir):
    return json.loads((run_dir / "verdict.json").read_text())


class TestProfileSchema:
    def test_the_schema_is_printed_as_json_and_nothing_runs(self, tmp_path, capsys):
        code = _main()(["--print-profile-schema", "--run-dir", str(tmp_path)])

        assert code == 0
        assert "iterations" in json.loads(capsys.readouterr().out)["properties"]
        assert not (tmp_path / "verdict.json").exists()


class TestRunning:
    def test_a_passing_run_exits_zero_and_writes_its_artifacts(self, tmp_path):
        code = _main()(["--run-dir", str(tmp_path)])

        assert code == 0
        assert _verdict(tmp_path)["passed"] is True

    def test_a_failing_run_exits_one(self, tmp_path):
        code = _main()(["--run-dir", str(tmp_path), "--set", "threshold_c=0"])

        assert code == 1
        assert _verdict(tmp_path)["passed"] is False

    def test_the_profile_file_is_loaded(self, tmp_path):
        profile = tmp_path / "quick.yaml"
        profile.write_text("iterations: 3\n")

        _main()(["--run-dir", str(tmp_path), "--profile", str(profile)])

        assert _verdict(tmp_path)["total_iterations"] == 3

    def test_a_default_profile_is_used_when_the_flag_is_absent(self, tmp_path):
        profile = tmp_path / "default.yaml"
        profile.write_text("iterations: 4\n")

        _main(default_profile=profile)(["--run-dir", str(tmp_path)])

        assert _verdict(tmp_path)["total_iterations"] == 4

    def test_target_and_unit_serial_reach_the_manifest(self, tmp_path):
        _main()(["--run-dir", str(tmp_path), "--target", "unit-3", "--unit-serial", "SN-9"])

        manifest = json.loads((tmp_path / "manifest.json").read_text())
        assert manifest["target"] == "unit-3"
        assert manifest["unit_serial"] == "SN-9"

    def test_an_invalid_profile_exits_two_without_running(self, tmp_path):
        profile = tmp_path / "bad.yaml"
        profile.write_text("iterationz: 3\n")

        code = _main()(["--run-dir", str(tmp_path), "--profile", str(profile)])

        assert code == 2
        assert not (tmp_path / "verdict.json").exists()


class TestOverrides:
    def test_duration_and_period_flags_become_overrides(self, tmp_path):
        _main()(["--run-dir", str(tmp_path), "--duration-s", "9", "--sample-period-s", "0"])

        assert json.loads((tmp_path / "manifest.json").read_text())["profile_summary"]["duration_s"] == "9.0"

    def test_set_applies_an_arbitrary_field(self, tmp_path):
        _main()(["--run-dir", str(tmp_path), "--set", "iterations=2"])

        assert _verdict(tmp_path)["total_iterations"] == 2

    def test_set_is_repeatable(self, tmp_path):
        _main()(["--run-dir", str(tmp_path), "--set", "iterations=2", "--set", "label=night"])

        summary = json.loads((tmp_path / "manifest.json").read_text())["profile_summary"]
        assert summary["label"] == "night"
        assert _verdict(tmp_path)["total_iterations"] == 2

    def test_set_without_an_equals_sign_exits_two(self, tmp_path, capsys):
        code = _main()(["--run-dir", str(tmp_path), "--set", "iterations"])

        assert code == 2
        assert "KEY=VALUE" in capsys.readouterr().out

    def test_set_with_an_empty_key_exits_two(self, tmp_path):
        assert _main()(["--run-dir", str(tmp_path), "--set", "=2"]) == 2

    def test_extra_args_can_add_a_suite_specific_flag(self, tmp_path):
        def _add(parser):
            parser.add_argument("--threshold-c", type=float, default=None)

        def _collect(args):
            return {"threshold_c": args.threshold_c}

        _main(extra_args=_add, extra_overrides=_collect)(
            ["--run-dir", str(tmp_path), "--threshold-c", "42"],
        )

        assert json.loads((tmp_path / "manifest.json").read_text())["profile_summary"]["threshold_c"] == "42.0"

    def test_an_extra_override_left_unset_does_not_replace_the_profile_value(self, tmp_path):
        def _add(parser):
            parser.add_argument("--threshold-c", type=float, default=None)

        def _collect(args):
            return {"threshold_c": args.threshold_c}

        _main(extra_args=_add, extra_overrides=_collect)(["--run-dir", str(tmp_path)])

        assert json.loads((tmp_path / "manifest.json").read_text())["profile_summary"]["threshold_c"] == "80.0"


class TestCoerce:
    @pytest.mark.parametrize("raw", ["true", "yes", "on", "TRUE", " On "])
    def test_the_truthy_words_become_true(self, raw):
        assert _coerce(raw) is True

    @pytest.mark.parametrize("raw", ["false", "no", "off", "OFF"])
    def test_the_falsy_words_become_false(self, raw):
        assert _coerce(raw) is False

    @pytest.mark.parametrize("raw", ["none", "null", "None"])
    def test_the_empty_words_become_none(self, raw):
        assert _coerce(raw) is None

    def test_integers_stay_integers(self):
        assert _coerce("42") == 42
        assert isinstance(_coerce("42"), int)

    def test_decimals_become_floats(self):
        assert _coerce("1.5") == 1.5

    def test_anything_else_stays_a_string(self):
        assert _coerce("unit-3") == "unit-3"

    def test_an_empty_value_stays_an_empty_string(self):
        assert _coerce("") == ""


class TestLog:
    """Levels travel on stdout as prefixes, which Gauntlet's log reader parses."""

    def test_info_is_printed_unprefixed(self, capsys):
        info("starting")

        assert capsys.readouterr().out == "starting\n"

    def test_warn_carries_the_prefix_the_reader_looks_for(self, capsys):
        warn("link flaky")

        assert capsys.readouterr().out == "warn: link flaky\n"

    def test_err_carries_the_prefix_the_reader_looks_for(self, capsys):
        err("link down")

        assert capsys.readouterr().out == "error: link down\n"
