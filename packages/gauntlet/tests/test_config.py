"""Settings resolution: defaults, config.yaml, then explicit overrides."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from gauntlet.config import Settings, default_data_dir, default_suite_roots, load_settings


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    """Point the data directory, and so config.yaml, at this test's tmp_path."""
    monkeypatch.setenv("GAUNTLET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("GAUNTLET_SUITE_PATH", raising=False)
    return tmp_path / "data"


class TestDefaults:
    def test_the_data_directory_is_output_under_the_working_directory(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_DATA_DIR", raising=False)

        assert default_data_dir() == Path.cwd() / "output"

    def test_the_data_directory_can_be_overridden(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_DATA_DIR", "~/gauntlet-data")

        assert default_data_dir() == Path.home() / "gauntlet-data"

    def test_suites_are_read_from_the_working_directory(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_SUITE_PATH", raising=False)

        assert default_suite_roots() == [Path.cwd() / "suites"]

    def test_the_suite_path_takes_several_roots(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_SUITE_PATH", os.pathsep.join(["/opt/rig-suites", "~/mine"]))

        assert default_suite_roots() == [Path("/opt/rig-suites"), Path.home() / "mine"]

    def test_empty_entries_in_the_suite_path_are_dropped(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_SUITE_PATH", f"/opt/rig-suites{os.pathsep}{os.pathsep}")

        assert default_suite_roots() == [Path("/opt/rig-suites")]


class TestDerivedPaths:
    def test_runs_and_profiles_live_under_the_data_directory(self, tmp_path):
        settings = Settings(data_dir=tmp_path)

        assert settings.runs_dir == tmp_path / "runs"
        assert settings.profiles_dir == tmp_path / "profiles"
        assert settings.config_path == tmp_path / "config.yaml"
        assert settings.runs_index_path == tmp_path / "runs.sqlite"
        assert settings.log_path == tmp_path / "logs" / "gauntlet.log"

    def test_each_derived_path_is_independently_overridable(self, tmp_path):
        settings = Settings(
            data_dir=tmp_path,
            reports_base=tmp_path / "elsewhere",
            profiles_user_dir=tmp_path / "profiles-elsewhere",
        )

        assert settings.runs_dir == tmp_path / "elsewhere"
        assert settings.profiles_dir == tmp_path / "profiles-elsewhere"

    def test_user_paths_are_expanded(self):
        settings = Settings(data_dir="~/data", suite_roots=["~/suites"])

        assert settings.data_dir == Path.home() / "data"
        assert settings.suite_roots == [Path.home() / "suites"]

    def test_suites_reach_the_api_over_loopback(self):
        assert Settings(host="0.0.0.0", port=7100).api_base == "http://127.0.0.1:7100/api"

    def test_ensure_dirs_creates_everything_written_to(self, tmp_path):
        settings = Settings(data_dir=tmp_path / "data")
        settings.ensure_dirs()

        assert settings.runs_dir.is_dir()
        assert settings.profiles_dir.is_dir()
        assert settings.log_path.parent.is_dir()

    def test_the_payload_reports_resolved_locations(self, tmp_path):
        payload = Settings(data_dir=tmp_path, suite_roots=[tmp_path / "suites"]).to_dict()

        assert payload["data_dir"] == str(tmp_path)
        assert payload["suite_roots"] == [str(tmp_path / "suites")]
        assert payload["runs_dir"] == str(tmp_path / "runs")
        assert payload["profiles_dir"] == str(tmp_path / "profiles")
        assert payload["runs_index_path"] == str(tmp_path / "runs.sqlite")


class TestLoadSettings:
    def test_with_no_config_file_the_defaults_stand(self, data_dir):
        assert load_settings().port == 7100

    def test_config_yaml_is_read(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yaml").write_text("port: 9000\nlog_level: debug\n")

        settings = load_settings()

        assert settings.port == 9000
        assert settings.log_level == "debug"

    def test_overrides_win_over_the_file(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yaml").write_text("port: 9000\n")

        assert load_settings({"port": 7200}).port == 7200

    def test_a_none_override_leaves_the_file_value_alone(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yaml").write_text("port: 9000\n")

        assert load_settings({"port": None}).port == 9000

    def test_unknown_keys_in_the_file_are_ignored(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yaml").write_text("port: 9000\nnot_a_setting: 1\n")

        assert load_settings().port == 9000

    def test_a_malformed_config_file_falls_back_to_the_defaults(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yaml").write_text("port: [9000\n")

        assert load_settings().port == 7100

    def test_a_config_file_that_is_not_a_mapping_falls_back_to_the_defaults(self, data_dir):
        data_dir.mkdir(parents=True)
        (data_dir / "config.yaml").write_text("- port\n")

        assert load_settings().port == 7100

    def test_an_unreadable_config_file_falls_back_to_the_defaults(self, data_dir):
        data_dir.mkdir(parents=True)
        config = data_dir / "config.yaml"
        config.write_text("port: 9000\n")
        config.chmod(0o000)

        try:
            assert load_settings().port == 7100
        finally:
            config.chmod(0o644)
