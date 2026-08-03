"""Profile loading, override application, and the manifest summary."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from gauntlet_sdk import ProfileError, load_profile, snapshot_profile, summarize_profile


class Profile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    description: str = "default"
    iterations: int = 3
    duration_s: float = 1.5
    verbose: bool = False


class TestLoadProfile:
    def test_no_path_uses_the_model_defaults(self):
        profile = load_profile(Profile, None)

        assert profile.iterations == 3
        assert profile.description == "default"

    def test_values_come_from_the_file(self, tmp_path):
        path = tmp_path / "quick.yaml"
        path.write_text("iterations: 9\ndescription: fast\n")

        profile = load_profile(Profile, path)

        assert profile.iterations == 9
        assert profile.description == "fast"

    def test_an_empty_file_falls_back_to_the_defaults(self, tmp_path):
        path = tmp_path / "empty.yaml"
        path.write_text("")

        assert load_profile(Profile, path).iterations == 3

    def test_overrides_win_over_the_file(self, tmp_path):
        path = tmp_path / "quick.yaml"
        path.write_text("iterations: 9\n")

        profile = load_profile(Profile, path, overrides={"iterations": 2})

        assert profile.iterations == 2

    def test_a_none_override_leaves_the_file_value_alone(self, tmp_path):
        path = tmp_path / "quick.yaml"
        path.write_text("iterations: 9\n")

        profile = load_profile(Profile, path, overrides={"iterations": None})

        assert profile.iterations == 9

    def test_overrides_apply_without_a_file(self):
        assert load_profile(Profile, None, overrides={"iterations": 11}).iterations == 11

    def test_a_missing_file_is_a_profile_error(self, tmp_path):
        with pytest.raises(ProfileError, match="cannot read profile"):
            load_profile(Profile, tmp_path / "absent.yaml")

    def test_malformed_yaml_is_a_profile_error(self, tmp_path):
        path = tmp_path / "broken.yaml"
        path.write_text("iterations: [1, 2\n")

        with pytest.raises(ProfileError, match="invalid YAML"):
            load_profile(Profile, path)

    def test_a_yaml_document_that_is_not_a_mapping_is_rejected(self, tmp_path):
        path = tmp_path / "list.yaml"
        path.write_text("- one\n- two\n")

        with pytest.raises(ProfileError, match="must be a mapping, got list"):
            load_profile(Profile, path)

    def test_an_unknown_field_is_rejected_rather_than_ignored(self, tmp_path):
        path = tmp_path / "typo.yaml"
        path.write_text("iterationz: 4\n")

        with pytest.raises(ProfileError, match="does not match Profile"):
            load_profile(Profile, path)

    def test_a_validation_failure_without_a_file_names_the_defaults(self):
        with pytest.raises(ProfileError, match="<defaults>"):
            load_profile(Profile, None, overrides={"iterations": "many"})


class TestSummarizeProfile:
    def test_every_scalar_field_is_stringified(self):
        summary = summarize_profile(Profile())

        assert summary == {
            "description": "default",
            "iterations": "3",
            "duration_s": "1.5",
            "verbose": "False",
        }

    def test_non_scalar_fields_are_left_out(self):
        class Nested(BaseModel):
            name: str = "x"
            targets: list[str] = ["a", "b"]

        assert summarize_profile(Nested()) == {"name": "x"}

    def test_a_field_list_selects_and_orders_the_output(self):
        summary = summarize_profile(Profile(), fields=["iterations", "description"])

        assert list(summary) == ["iterations", "description"]

    def test_a_requested_field_the_model_lacks_is_skipped(self):
        assert summarize_profile(Profile(), fields=["nonexistent"]) == {}


class TestSnapshotProfile:
    def test_the_profile_is_copied_into_the_run_directory(self, tmp_path):
        source = tmp_path / "quick.yaml"
        source.write_text("iterations: 2\n")
        run_dir = tmp_path / "run"
        run_dir.mkdir()

        dest = snapshot_profile(source, run_dir)

        assert dest == run_dir / "profile.yaml"
        assert dest.read_text() == "iterations: 2\n"

    def test_an_existing_snapshot_is_left_as_it_is(self, tmp_path):
        source = tmp_path / "quick.yaml"
        source.write_text("iterations: 2\n")
        run_dir = tmp_path / "run"
        run_dir.mkdir()
        (run_dir / "profile.yaml").write_text("taken earlier\n")

        dest = snapshot_profile(source, run_dir)

        assert dest.read_text() == "taken earlier\n"

    def test_no_source_means_no_snapshot(self, tmp_path):
        assert snapshot_profile(None, tmp_path) is None

    def test_a_source_that_is_not_a_file_means_no_snapshot(self, tmp_path):
        assert snapshot_profile(tmp_path / "absent.yaml", tmp_path) is None

    def test_an_unwritable_run_directory_is_not_an_error(self, tmp_path):
        source = tmp_path / "quick.yaml"
        source.write_text("iterations: 2\n")
        run_dir = tmp_path / "readonly"
        run_dir.mkdir(mode=0o500)

        try:
            assert snapshot_profile(source, run_dir) is None
        finally:
            run_dir.chmod(0o700)
