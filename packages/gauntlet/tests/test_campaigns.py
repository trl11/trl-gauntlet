"""Campaign discovery and the ``campaign.yaml`` loader."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from gauntlet.campaigns import CampaignError, discover_campaigns, load_campaign, load_manifest


def _write(directory: Path, manifest: dict) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "campaign.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
    return directory


def _manifest(key: str = "demo", **overrides) -> dict:
    manifest = {"apiVersion": 1, "key": key, "title": key.title(), "suites": "./suites"}
    manifest.update(overrides)
    return manifest


class TestLoadManifest:
    def test_a_valid_manifest_loads(self, tmp_path: Path):
        directory = _write(tmp_path / "demo", _manifest())

        campaign = load_campaign(directory)

        assert campaign.key == "demo"
        assert campaign.suites_dir == (directory / "suites").resolve()

    def test_a_missing_manifest_is_an_error(self, tmp_path: Path):
        with pytest.raises(CampaignError, match=r"no campaign\.yaml"):
            load_campaign(tmp_path)

    def test_an_unsupported_api_version_is_refused(self, tmp_path: Path):
        directory = _write(tmp_path / "demo", _manifest(apiVersion=2))

        with pytest.raises(CampaignError, match="apiVersion"):
            load_campaign(directory)

    def test_an_unknown_field_is_refused(self, tmp_path: Path):
        directory = _write(tmp_path / "demo", _manifest(schedule="nightly"))

        with pytest.raises(CampaignError, match="schedule"):
            load_campaign(directory)

    def test_invalid_yaml_names_the_file(self, tmp_path: Path):
        path = tmp_path / "campaign.yaml"
        path.write_text("key: [unclosed\n")

        with pytest.raises(CampaignError, match="invalid YAML"):
            load_manifest(path)

    def test_a_member_key_must_be_a_suite_key(self, tmp_path: Path):
        directory = _write(tmp_path / "demo", _manifest(members=[{"suite": "Not A Key"}]))

        with pytest.raises(CampaignError, match=r"members\.0\.suite"):
            load_campaign(directory)


class TestOwnership:
    def test_a_suite_inside_the_suite_directory_is_owned(self, tmp_path: Path):
        campaign = load_campaign(_write(tmp_path / "demo", _manifest()))

        assert campaign.owns(campaign.suites_dir / "alpha")

    def test_a_suite_outside_it_is_not(self, tmp_path: Path):
        campaign = load_campaign(_write(tmp_path / "demo", _manifest()))

        assert not campaign.owns(tmp_path / "elsewhere" / "alpha")


class TestDiscovery:
    def test_a_missing_root_is_skipped_rather_than_raised(self, tmp_path: Path):
        catalog = discover_campaigns([tmp_path / "absent"])

        assert catalog.campaigns == {}
        assert catalog.errors == []

    def test_every_campaign_under_a_root_is_found(self, tmp_path: Path):
        _write(tmp_path / "one", _manifest("one"))
        _write(tmp_path / "two", _manifest("two"))

        catalog = discover_campaigns([tmp_path])

        assert sorted(catalog.campaigns) == ["one", "two"]

    def test_a_malformed_manifest_is_collected_not_raised(self, tmp_path: Path):
        _write(tmp_path / "good", _manifest("good"))
        _write(tmp_path / "bad", _manifest("bad", apiVersion=99))

        catalog = discover_campaigns([tmp_path])

        assert sorted(catalog.campaigns) == ["good"]
        assert len(catalog.errors) == 1
        assert "apiVersion" in catalog.errors[0]

    def test_a_duplicate_key_keeps_the_earlier_root(self, tmp_path: Path):
        first = _write(tmp_path / "a" / "demo", _manifest())
        _write(tmp_path / "b" / "demo", _manifest())

        catalog = discover_campaigns([tmp_path / "a", tmp_path / "b"])

        assert catalog.campaigns["demo"].directory == first.resolve()
        assert "duplicate campaign key" in catalog.errors[0]

    def test_discovery_does_not_descend_into_a_campaign(self, tmp_path: Path):
        _write(tmp_path / "outer", _manifest("outer"))
        _write(tmp_path / "outer" / "nested", _manifest("nested"))

        catalog = discover_campaigns([tmp_path])

        assert sorted(catalog.campaigns) == ["outer"]

    def test_suite_roots_are_the_campaigns_suite_directories(self, tmp_path: Path):
        _write(tmp_path / "one", _manifest("one"))
        _write(tmp_path / "two", _manifest("two", suites="./components"))

        catalog = discover_campaigns([tmp_path])

        assert catalog.suite_roots() == [
            (tmp_path / "one" / "suites").resolve(),
            (tmp_path / "two" / "components").resolve(),
        ]

    def test_for_path_finds_the_owning_campaign(self, tmp_path: Path):
        _write(tmp_path / "one", _manifest("one"))
        _write(tmp_path / "two", _manifest("two"))

        catalog = discover_campaigns([tmp_path])
        owner = catalog.for_path(tmp_path / "two" / "suites" / "alpha")

        assert owner is not None
        assert owner.key == "two"

    def test_for_path_returns_none_for_an_unowned_suite(self, tmp_path: Path):
        _write(tmp_path / "one", _manifest("one"))

        catalog = discover_campaigns([tmp_path])

        assert catalog.for_path(tmp_path / "loose" / "alpha") is None


class TestBuiltInCampaigns:
    """The campaigns shipped in the repository."""

    def test_the_hardware_campaign_loads(self):
        campaign = load_campaign(Path(__file__).resolve().parents[3] / "campaigns" / "hardware")

        assert campaign.key == "hardware"
        assert {m.suite for m in campaign.manifest.members} == {
            "can_bus",
            "ethernet",
            "hardware_trigger",
            "piezo",
            "rs422",
            "ssd",
        }

    def test_every_declared_member_is_on_disk(self):
        campaign = load_campaign(Path(__file__).resolve().parents[3] / "campaigns" / "hardware")

        for member in campaign.manifest.members:
            assert (campaign.suites_dir / member.suite / "suite.yaml").is_file()
