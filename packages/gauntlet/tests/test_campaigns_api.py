"""Campaign endpoints: catalog, coverage, manifest editing, and member runs."""

from __future__ import annotations

import yaml


def _keys(client) -> list[str]:
    return [campaign["key"] for campaign in client.get("/api/campaigns").json()["campaigns"]]


def _members(client, key: str = "demo_campaign") -> list[dict]:
    return client.get(f"/api/campaigns/{key}").json()["members"]


class TestCatalog:
    def test_no_campaigns_is_an_empty_catalog(self, client):
        response = client.get("/api/campaigns")

        assert response.status_code == 200
        assert response.json() == {"campaigns": [], "errors": []}

    def test_a_campaign_added_after_startup_is_found(self, client, make_campaign):
        make_campaign("demo_campaign")

        response = client.post("/api/campaigns/rescan")

        assert response.status_code == 200
        assert [c["key"] for c in response.json()["campaigns"]] == ["demo_campaign"]

    def test_an_unknown_campaign_is_a_404(self, client):
        assert client.get("/api/campaigns/absent").status_code == 404

    def test_a_malformed_campaign_is_reported_not_fatal(self, client, campaign_root):
        directory = campaign_root / "broken"
        directory.mkdir()
        (directory / "campaign.yaml").write_text("apiVersion: 99\nkey: broken\ntitle: Broken\n")

        payload = client.post("/api/campaigns/rescan").json()

        assert payload["campaigns"] == []
        assert "apiVersion" in payload["errors"][0]

    def test_the_schema_endpoint_describes_the_manifest(self, client):
        response = client.get("/api/campaigns/schema")

        assert response.status_code == 200
        assert "key" in response.json()["properties"]


class TestMembership:
    def test_a_suite_in_the_campaign_directory_joins_it(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        assert [m["suite"] for m in _members(client)] == ["beta"]

    def test_a_campaign_suite_becomes_runnable(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        assert "beta" in [suite["key"] for suite in client.get("/api/suites").json()["suites"]]

    def test_a_suite_dropped_in_later_joins_without_a_restart(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign")
        client.post("/api/campaigns/rescan")
        assert _members(client) == []

        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")

        assert [m["suite"] for m in _members(client)] == ["beta"]

    def test_declared_members_come_first_in_manifest_order(self, client, make_campaign, make_suite):
        campaign = make_campaign(
            "demo_campaign",
            members=[{"suite": "gamma"}, {"suite": "beta"}],
        )
        for key in ("alpha_two", "beta", "gamma"):
            make_suite(key, root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        assert [m["suite"] for m in _members(client)] == ["gamma", "beta", "alpha_two"]

    def test_an_undeclared_member_is_marked_as_such(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        member = _members(client)[0]
        assert member["declared"] is False
        assert member["present"] is True

    def test_a_declared_member_with_no_suite_is_listed_as_absent(self, client, make_campaign):
        make_campaign("demo_campaign", members=[{"suite": "missing"}])

        client.post("/api/campaigns/rescan")

        member = _members(client)[0]
        assert member["present"] is False
        assert member["declared"] is True

    def test_declared_configuration_reaches_the_member(self, client, make_campaign, make_suite):
        campaign = make_campaign(
            "demo_campaign",
            members=[{"suite": "beta", "component": "LAN7430-I/Y9X", "fixture": "1-1", "profile": "quick.yaml"}],
        )
        make_suite("beta", root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        member = _members(client)[0]
        assert member["component"] == "LAN7430-I/Y9X"
        assert member["fixture"] == "1-1"
        assert member["profile"] == "quick.yaml"


class TestCoverage:
    def test_a_member_with_no_runs_reports_zero(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        member = _members(client)[0]
        assert member["run_count"] == 0
        assert member["last_run"] is None

    def test_coverage_counts_runs_by_suite(self, client, make_campaign, make_suite, add_run):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")

        add_run("r1", suite="beta", status="passed")
        add_run("r2", suite="beta", status="failed")

        member = _members(client)[0]
        assert member["run_count"] == 2
        assert member["passed"] == 1
        assert member["failed"] == 1
        assert member["last_run"]["run_id"] == "r2"

    def test_coverage_survives_rebuilding_the_index_from_disk(self, client, make_campaign, make_suite, add_run):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")
        add_run("r1", suite="beta", status="passed")

        # Membership is derived from the suite key, so nothing about the
        # campaign is stored on the run row for a reimport to lose.
        client.post("/api/campaigns/rescan")

        assert _members(client)[0]["run_count"] == 1


class TestManifestEditing:
    def test_the_manifest_comes_back_as_text(self, client, make_campaign):
        make_campaign("demo_campaign")
        client.post("/api/campaigns/rescan")

        response = client.get("/api/campaigns/demo_campaign/manifest")

        assert response.status_code == 200
        assert "key: demo_campaign" in response.json()["body"]

    def test_an_edit_is_saved_and_takes_effect(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")

        edited = yaml.safe_dump(
            {
                "apiVersion": 1,
                "key": "demo_campaign",
                "title": "Renamed",
                "suites": "./suites",
                "members": [{"suite": "beta", "fixture": "9-9"}],
            },
            sort_keys=False,
        )
        response = client.put("/api/campaigns/demo_campaign/manifest", json={"body": edited})

        assert response.status_code == 200
        assert response.json()["title"] == "Renamed"
        assert _members(client)[0]["fixture"] == "9-9"

    def test_an_invalid_edit_is_refused_and_the_file_is_unchanged(self, client, make_campaign):
        make_campaign("demo_campaign")
        client.post("/api/campaigns/rescan")
        before = client.get("/api/campaigns/demo_campaign/manifest").json()["body"]

        response = client.put(
            "/api/campaigns/demo_campaign/manifest",
            json={"body": "apiVersion: 1\nkey: demo_campaign\ntitle: X\nschedule: nightly\n"},
        )

        assert response.status_code == 422
        assert client.get("/api/campaigns/demo_campaign/manifest").json()["body"] == before

    def test_invalid_yaml_is_refused(self, client, make_campaign):
        make_campaign("demo_campaign")
        client.post("/api/campaigns/rescan")

        response = client.put("/api/campaigns/demo_campaign/manifest", json={"body": "key: [unclosed\n"})

        assert response.status_code == 422
        assert "invalid YAML" in response.json()["detail"]

    def test_renaming_the_key_is_refused(self, client, make_campaign):
        make_campaign("demo_campaign")
        client.post("/api/campaigns/rescan")

        response = client.put(
            "/api/campaigns/demo_campaign/manifest",
            json={"body": "apiVersion: 1\nkey: renamed\ntitle: X\n"},
        )

        assert response.status_code == 422
        assert "must stay" in response.json()["detail"]


class TestMemberRuns:
    def test_a_member_run_uses_the_declared_profile(self, client, make_campaign, make_suite):
        campaign = make_campaign(
            "demo_campaign",
            members=[{"suite": "beta", "profile": "quick.yaml"}],
        )
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")

        response = client.post("/api/campaigns/demo_campaign/members/beta/run", json={})

        assert response.status_code == 201
        assert response.json()["suite"] == "beta"
        assert response.json()["profile"] == "quick.yaml"

    def test_a_non_member_suite_is_a_404(self, client, make_campaign, make_suite):
        make_campaign("demo_campaign")
        make_suite("loose")
        client.post("/api/campaigns/rescan")

        response = client.post("/api/campaigns/demo_campaign/members/loose/run", json={})

        assert response.status_code == 404

    def test_a_declared_member_with_no_suite_is_refused(self, client, make_campaign):
        make_campaign("demo_campaign", members=[{"suite": "missing"}])
        client.post("/api/campaigns/rescan")

        response = client.post("/api/campaigns/demo_campaign/members/missing/run", json={})

        assert response.status_code == 422
