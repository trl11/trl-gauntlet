"""Finding suites on disk, and failing usefully when one is malformed."""

from __future__ import annotations

import pytest
import yaml

from gauntlet.suites import ManifestError, discover_suites, list_profiles, load_suite, resolve_profile


class TestDiscovery:
    def test_finds_a_suite(self, make_suite, suite_root):
        make_suite("alpha")
        catalog = discover_suites([suite_root])
        assert list(catalog.suites) == ["alpha"]
        assert not catalog.errors

    def test_finds_several(self, make_suite, suite_root):
        make_suite("alpha")
        make_suite("beta")
        assert sorted(discover_suites([suite_root]).suites) == ["alpha", "beta"]

    def test_missing_root_is_not_an_error(self, tmp_path):
        catalog = discover_suites([tmp_path / "nope"])
        assert catalog.suites == {}
        assert not catalog.errors

    def test_broken_manifest_does_not_hide_working_suites(self, make_suite, suite_root):
        make_suite("good")
        broken = suite_root / "broken"
        broken.mkdir()
        (broken / "suite.yaml").write_text("apiVersion: 1\nkey: broken\n")

        catalog = discover_suites([suite_root])
        assert "good" in catalog.suites
        assert "broken" not in catalog.suites
        assert len(catalog.errors) == 1

    def test_duplicate_key_keeps_the_first_root(self, make_suite, suite_root, tmp_path):
        make_suite("dupe")
        second = tmp_path / "other"
        (second / "dupe" / "profiles").mkdir(parents=True)
        (second / "dupe" / "suite.yaml").write_text(
            yaml.safe_dump({"apiVersion": 1, "key": "dupe", "title": "Other", "exec": {"command": ["true"]}})
        )

        catalog = discover_suites([suite_root, second])
        assert catalog.suites["dupe"].directory == (suite_root / "dupe")
        assert any("duplicate" in e for e in catalog.errors)

    def test_does_not_descend_into_a_suite(self, make_suite, suite_root):
        suite = make_suite("outer")
        nested = suite.directory / "inner"
        nested.mkdir()
        (nested / "suite.yaml").write_text(
            yaml.safe_dump({"apiVersion": 1, "key": "inner", "title": "Inner", "exec": {"command": ["true"]}})
        )
        assert sorted(discover_suites([suite_root]).suites) == ["outer"]


class TestManifestErrors:
    def test_missing_file(self, tmp_path):
        with pytest.raises(ManifestError, match=r"no suite\.yaml"):
            load_suite(tmp_path)

    def test_wrong_api_version(self, suite_root):
        directory = suite_root / "old"
        directory.mkdir()
        (directory / "suite.yaml").write_text(
            yaml.safe_dump({"apiVersion": 99, "key": "old", "title": "Old", "exec": {"command": ["true"]}})
        )
        with pytest.raises(ManifestError, match="apiVersion"):
            load_suite(directory)

    def test_validation_error_names_the_field(self, suite_root):
        directory = suite_root / "bad"
        directory.mkdir()
        (directory / "suite.yaml").write_text(
            yaml.safe_dump({"apiVersion": 1, "key": "Bad Key", "title": "Bad", "exec": {"command": ["true"]}})
        )
        with pytest.raises(ManifestError, match="key"):
            load_suite(directory)


class TestProfiles:
    def test_lists_suite_profiles(self, make_suite):
        suite = make_suite("alpha")
        profiles = list_profiles(suite)
        assert [p.name for p in profiles] == ["quick.yaml"]
        assert profiles[0].description == "fast"

    def test_user_profile_shadows_the_shipped_one(self, make_suite, tmp_path):
        suite = make_suite("alpha")
        user_dir = tmp_path / "user"
        (user_dir / "alpha").mkdir(parents=True)
        (user_dir / "alpha" / "quick.yaml").write_text("description: mine\n")

        profiles = list_profiles(suite, user_dir)
        assert len(profiles) == 1
        assert profiles[0].user_authored
        assert profiles[0].description == "mine"

    def test_resolves_with_or_without_the_extension(self, make_suite):
        suite = make_suite("alpha")
        assert resolve_profile(suite, "quick.yaml") is not None
        assert resolve_profile(suite, "quick") is not None

    def test_rejects_a_traversal_attempt(self, make_suite):
        suite = make_suite("alpha")
        assert resolve_profile(suite, "../../etc/passwd") is None
