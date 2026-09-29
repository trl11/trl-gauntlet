"""Marking runs as favorites and listing only those."""

from __future__ import annotations


class TestFavorites:
    def test_a_run_starts_as_no_favorite(self, client, add_run) -> None:
        add_run("r1")
        assert client.get("/api/runs/r1").json()["favorite"] is False

    def test_marking_and_unmarking(self, client, add_run) -> None:
        add_run("r1")
        assert client.put("/api/runs/r1/favorite").json() == {"run_id": "r1", "favorite": True}
        assert client.get("/api/runs/r1").json()["favorite"] is True
        assert client.delete("/api/runs/r1/favorite").json() == {"run_id": "r1", "favorite": False}
        assert client.get("/api/runs/r1").json()["favorite"] is False

    def test_an_unknown_run_is_404(self, client) -> None:
        assert client.put("/api/runs/nope/favorite").status_code == 404
        assert client.delete("/api/runs/nope/favorite").status_code == 404

    def test_a_listing_says_which_runs_are_favorites(self, client, add_run) -> None:
        add_run("r1")
        add_run("r2")
        client.put("/api/runs/r2/favorite")
        listed = client.get("/api/runs").json()["runs"]
        assert {row["run_id"]: row["favorite"] for row in listed} == {"r1": False, "r2": True}

    def test_the_filter_keeps_only_favorites(self, client, add_run) -> None:
        add_run("r1")
        add_run("r2")
        client.put("/api/runs/r2/favorite")
        listed = client.get("/api/runs", params={"favorite": "true"}).json()
        assert [row["run_id"] for row in listed["runs"]] == ["r2"]
        assert listed["total"] == 1
