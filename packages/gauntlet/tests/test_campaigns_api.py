"""Campaign endpoints: catalog, coverage, manifest editing, and member runs."""

from __future__ import annotations


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

    def test_an_undeclared_member_still_carries_every_field(self, client, make_campaign, make_suite):
        campaign = make_campaign("demo_campaign", members=[{"suite": "beta", "fixture": "1-1"}])
        make_suite("beta", root=campaign.suites_dir)
        make_suite("gamma", root=campaign.suites_dir)

        client.post("/api/campaigns/rescan")

        declared, undeclared = _members(client)
        assert set(declared) == set(undeclared)
        assert undeclared["fixture"] == ""

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


class TestMemberPayload:
    def test_a_member_carries_no_run_history(self, client, make_campaign, make_suite, add_run):
        # A campaign groups suites and says how to run them. What those suites
        # have done belongs to the run, which names its campaign instead.
        campaign = make_campaign("demo_campaign")
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")
        add_run("r1", suite="beta")

        member = _members(client)[0]

        assert "run_count" not in member
        assert "last_run" not in member


class TestManifestIsReadOnly:
    def test_the_router_offers_no_way_to_write_one(self, client, make_campaign):
        # campaign.yaml is edited with an editor and picked up by a rescan, so
        # nothing here can disagree with the file on disk.
        make_campaign("demo_campaign")
        client.post("/api/campaigns/rescan")

        assert client.get("/api/campaigns/demo_campaign/manifest").status_code == 404
        # The SPA catch-all claims every path for GET alone, so another method is
        # refused before any handler sees it.
        assert client.put("/api/campaigns/demo_campaign/manifest", json={"body": ""}).status_code == 405


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


class TestCampaignOnARun:
    """A run names the campaign that groups its suite."""

    def test_a_run_of_a_campaign_suite_names_it(self, client, make_campaign, make_suite, add_run):
        campaign = make_campaign("demo_campaign", title="Demo Campaign")
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")
        add_run("r1", suite="beta")

        row = client.get("/api/runs").json()["runs"][0]

        assert row["campaign"] == {"key": "demo_campaign", "title": "Demo Campaign"}

    def test_a_run_of_a_loose_suite_names_none(self, client, add_run):
        add_run("r1", suite="alpha")

        assert client.get("/api/runs").json()["runs"][0]["campaign"] is None

    def test_the_run_detail_carries_it_too(self, client, make_campaign, make_suite, add_run):
        campaign = make_campaign("demo_campaign", title="Demo Campaign")
        make_suite("beta", root=campaign.suites_dir)
        client.post("/api/campaigns/rescan")
        add_run("r1", suite="beta")

        assert client.get("/api/runs/r1").json()["campaign"]["key"] == "demo_campaign"

    def test_it_follows_the_suite_rather_than_being_recorded(
        self, client, make_campaign, make_suite, add_run, campaign_root
    ):
        # The run is indexed before any campaign exists, then the campaign
        # appears around its suite. Nothing was written to the run, so the
        # association is simply read afresh.
        campaign = make_campaign("demo_campaign", title="Demo Campaign")
        make_suite("beta", root=campaign.suites_dir)
        add_run("r1", suite="beta")
        assert client.get("/api/runs/r1").json()["campaign"] is None

        client.post("/api/campaigns/rescan")

        assert client.get("/api/runs/r1").json()["campaign"]["key"] == "demo_campaign"
