"""Reading the environment half of the contract."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from gauntlet_suite.environment import run_environment
from gauntlet_suite.profile import ProfileError, load_profile, summarize_profile


class TestRunEnvironment:
    def test_supervised_run_uses_the_directory_it_is_given(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAUNTLET_RUN_DIR", str(tmp_path / "given"))
        monkeypatch.setenv("GAUNTLET_RUN_ID", "run-1")
        monkeypatch.setenv("GAUNTLET_SUITE", "demo")

        env = run_environment()
        assert env.supervised
        assert env.run_dir == tmp_path / "given"
        assert env.run_dir.is_dir()
        assert env.run_id == "run-1"

    def test_standalone_run_invents_a_directory(self, tmp_path, monkeypatch):
        monkeypatch.delenv("GAUNTLET_RUN_DIR", raising=False)
        monkeypatch.chdir(tmp_path)

        env = run_environment(suite="demo")
        assert not env.supervised
        assert env.run_dir.is_dir()
        assert "gauntlet-runs" in str(env.run_dir)

    def test_explicit_arguments_beat_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAUNTLET_RUN_DIR", str(tmp_path / "from-env"))
        monkeypatch.setenv("GAUNTLET_TARGET", "10.0.0.1")

        env = run_environment(run_dir=tmp_path / "from-flag", target="10.0.0.2")
        assert env.run_dir == tmp_path / "from-flag"
        assert env.target == "10.0.0.2"

    def test_capabilities_are_parsed_from_the_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAUNTLET_RUN_DIR", str(tmp_path))
        monkeypatch.setenv("GAUNTLET_CAP_PSU_URL", "http://localhost/api/capabilities/psu")
        monkeypatch.setenv("GAUNTLET_CAP_PSU_ID", "psu0")

        env = run_environment()
        assert env.capability("psu").url.endswith("/psu")
        assert env.capability("psu").instance_id == "psu0"

    def test_missing_capability_names_what_was_granted(self, tmp_path, monkeypatch):
        monkeypatch.setenv("GAUNTLET_RUN_DIR", str(tmp_path))
        monkeypatch.setenv("GAUNTLET_CAP_DAQ_URL", "http://localhost/api/capabilities/daq")

        with pytest.raises(LookupError, match="granted: daq"):
            run_environment().capability("psu")


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    duration_s: float = 60.0
    label: str = "default"


class TestProfileLoading:
    def test_missing_path_uses_model_defaults(self):
        assert load_profile(Profile, None).duration_s == 60.0

    def test_overrides_win_over_the_file(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("duration_s: 10.0\n")
        assert load_profile(Profile, path, overrides={"duration_s": 99.0}).duration_s == 99.0

    def test_none_overrides_are_ignored(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("duration_s: 10.0\n")
        assert load_profile(Profile, path, overrides={"duration_s": None}).duration_s == 10.0

    def test_unknown_key_names_the_file(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("duration_s: 1.0\ntpyo: 2\n")
        with pytest.raises(ProfileError, match="tpyo"):
            load_profile(Profile, path)

    def test_invalid_yaml_is_reported_clearly(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("duration_s: [unclosed\n")
        with pytest.raises(ProfileError, match="invalid YAML"):
            load_profile(Profile, path)

    def test_empty_file_is_all_defaults(self, tmp_path):
        path = tmp_path / "p.yaml"
        path.write_text("")
        assert load_profile(Profile, path).duration_s == 60.0

    def test_summary_flattens_scalars(self):
        assert summarize_profile(Profile()) == {"duration_s": "60.0", "label": "default"}
