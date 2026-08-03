"""Turning a request plus a manifest into argv and an environment."""

from __future__ import annotations

import pytest

from gauntlet.supervisor.launcher import LaunchError, RunRequest, build_launch

OVERRIDES = [
    {"name": "duration_s", "flag": "--duration-s", "type": "number"},
    {"name": "cycles", "flag": "--cycles", "type": "integer"},
    {"name": "verbose", "flag": "--verbose", "type": "boolean"},
    {"name": "mode", "flag": "--mode", "type": "string", "choices": ["fast", "slow"]},
]


def _launch(suite, tmp_path, **request_kwargs):
    return build_launch(
        suite,
        RunRequest(suite=suite.key, **request_kwargs),
        run_id="run-1",
        run_dir=tmp_path / "run",
        profile_path=request_kwargs.pop("profile_path", None),
        api_base="http://127.0.0.1:7100/api",
    )


class TestArgv:
    def test_declared_args_are_appended(self, make_suite, tmp_path):
        suite = make_suite("demo")
        launch = _launch(suite, tmp_path)
        assert launch.argv[0] == "./run.sh"
        assert "--run-dir" in launch.argv
        assert str(tmp_path / "run") in launch.argv

    def test_unset_values_omit_their_flag(self, make_suite, tmp_path):
        suite = make_suite("demo", exec={"command": ["./run.sh"], "args": {"target": "--target"}})
        assert "--target" not in _launch(suite, tmp_path).argv

    def test_set_values_include_their_flag(self, make_suite, tmp_path):
        suite = make_suite("demo", exec={"command": ["./run.sh"], "args": {"target": "--target"}})
        argv = _launch(suite, tmp_path, target="10.0.0.5").argv
        assert argv[-2:] == ["--target", "10.0.0.5"]


class TestOverrides:
    def test_numeric_override(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        argv = _launch(suite, tmp_path, overrides={"duration_s": 12.5}).argv
        assert argv[-2:] == ["--duration-s", "12.5"]

    def test_integer_override_is_coerced(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        argv = _launch(suite, tmp_path, overrides={"cycles": "7"}).argv
        assert argv[-2:] == ["--cycles", "7"]

    def test_true_boolean_is_a_bare_flag(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        assert _launch(suite, tmp_path, overrides={"verbose": True}).argv[-1] == "--verbose"

    def test_false_boolean_is_omitted(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        assert "--verbose" not in _launch(suite, tmp_path, overrides={"verbose": False}).argv

    def test_undeclared_override_is_rejected(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        with pytest.raises(LaunchError, match="does not declare override"):
            _launch(suite, tmp_path, overrides={"rm_rf": "/"})

    def test_bad_number_is_rejected(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        with pytest.raises(LaunchError, match="expects a number"):
            _launch(suite, tmp_path, overrides={"duration_s": "abc"})

    def test_choice_is_enforced(self, make_suite, tmp_path):
        suite = make_suite("demo", overrides=OVERRIDES)
        with pytest.raises(LaunchError, match="must be one of"):
            _launch(suite, tmp_path, overrides={"mode": "sideways"})
        assert "--mode" in _launch(suite, tmp_path, overrides={"mode": "fast"}).argv


class TestEnvironment:
    def test_contract_variables_are_set(self, make_suite, tmp_path):
        suite = make_suite("demo")
        env = _launch(suite, tmp_path, target="10.0.0.5", unit_serial="SN1").env
        assert env["GAUNTLET_RUN_DIR"] == str(tmp_path / "run")
        assert env["GAUNTLET_RUN_ID"] == "run-1"
        assert env["GAUNTLET_SUITE"] == "demo"
        assert env["GAUNTLET_SUITE_DIR"] == str(suite.directory)
        assert env["GAUNTLET_TARGET"] == "10.0.0.5"
        assert env["GAUNTLET_UNIT_SERIAL"] == "SN1"

    def test_suite_directory_is_importable(self, make_suite, tmp_path):
        suite = make_suite("demo")
        assert str(suite.directory) in _launch(suite, tmp_path).env["PYTHONPATH"]

    def test_colour_is_disabled(self, make_suite, tmp_path):
        assert _launch(make_suite("demo"), tmp_path).env["NO_COLOR"] == "1"

    def test_capability_variables_are_merged(self, make_suite, tmp_path):
        suite = make_suite("demo")
        launch = build_launch(
            suite,
            RunRequest(suite="demo"),
            run_id="r",
            run_dir=tmp_path / "run",
            profile_path=None,
            api_base="http://127.0.0.1:7100/api",
            capability_env={"GAUNTLET_CAP_PSU_URL": "http://x/psu", "GAUNTLET_CAP_PSU_ID": "psu0"},
        )
        assert launch.env["GAUNTLET_CAP_PSU_URL"] == "http://x/psu"
