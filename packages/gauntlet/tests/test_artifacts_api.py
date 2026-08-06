"""Reading a finished run's files, and refusing to read anything else."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def artifacts(client, add_run, tmp_path: Path) -> Path:
    """A run directory holding one of each thing the endpoints serve."""
    run_dir = tmp_path / "artifacts"
    (run_dir / "nested").mkdir(parents=True)
    (run_dir / "verdict.json").write_text('{"passed": true, "reason": ""}')
    (run_dir / "manifest.json").write_text('{"suite": "alpha", "run_id": "r1"}')
    (run_dir / "metrics.jsonl").write_text(
        '{"kind":"iteration","iteration":1}\n\n  \nnot json\n["a list"]\n{"kind":"iteration","iteration":2}\n'
    )
    (run_dir / "test.log").write_text("started\nfinished\n")
    (run_dir / "capture.bin").write_bytes(b"\x00\x01\x02\x03")
    (run_dir / "nested" / "extra.txt").write_text("nested file\n")
    add_run("r1", run_dir=run_dir)
    return run_dir


class TestListing:
    def test_lists_every_file_with_its_size(self, client, artifacts: Path) -> None:
        body = client.get("/api/runs/r1/artifacts").json()
        by_path = {entry["path"]: entry for entry in body["artifacts"]}
        assert by_path["verdict.json"]["size"] == (artifacts / "verdict.json").stat().st_size
        assert by_path["nested/extra.txt"]["text"] is True
        assert by_path["capture.bin"]["text"] is False

    def test_directories_are_not_artifacts(self, client, artifacts: Path) -> None:
        paths = {entry["path"] for entry in client.get("/api/runs/r1/artifacts").json()["artifacts"]}
        assert "nested" not in paths

    def test_an_unknown_run_is_404(self, client) -> None:
        assert client.get("/api/runs/nope/artifacts").status_code == 404

    def test_a_run_whose_directory_is_gone_is_404(self, client, add_run) -> None:
        add_run("vanished", run_dir="/tmp/definitely-not-here")
        assert client.get("/api/runs/vanished/artifacts").status_code == 404


class TestReadingOne:
    def test_text_is_returned_inline(self, client, artifacts: Path) -> None:
        response = client.get("/api/runs/r1/artifacts/test.log")
        assert response.status_code == 200
        assert response.text == "started\nfinished\n"

    def test_a_nested_path_is_reachable(self, client, artifacts: Path) -> None:
        assert client.get("/api/runs/r1/artifacts/nested/extra.txt").text == "nested file\n"

    def test_anything_else_comes_back_as_a_file(self, client, artifacts: Path) -> None:
        response = client.get("/api/runs/r1/artifacts/capture.bin")
        assert response.status_code == 200
        assert response.content == b"\x00\x01\x02\x03"

    def test_a_missing_file_is_404(self, client, artifacts: Path) -> None:
        assert client.get("/api/runs/r1/artifacts/nope.txt").status_code == 404

    def test_a_path_escaping_the_run_directory_is_refused(self, client, artifacts: Path) -> None:
        response = client.get("/api/runs/r1/artifacts/..%2F..%2Fetc%2Fpasswd")
        assert response.status_code in {400, 404}

    def test_a_directory_is_not_a_file(self, client, artifacts: Path) -> None:
        assert client.get("/api/runs/r1/artifacts/nested").status_code == 404


class TestParsedFiles:
    def test_verdict_is_served_as_the_file(self, client, artifacts: Path) -> None:
        assert client.get("/api/runs/r1/artifacts/verdict.json").json()["passed"] is True

    def test_manifest_is_served_as_the_file(self, client, artifacts: Path) -> None:
        assert client.get("/api/runs/r1/artifacts/manifest.json").json()["suite"] == "alpha"

    def test_metrics_keeps_only_the_json_objects(self, client, artifacts: Path) -> None:
        body = client.get("/api/runs/r1/metrics").json()
        assert body["count"] == 2
        assert [record["iteration"] for record in body["records"]] == [1, 2]

    def test_metrics_honours_the_limit(self, client, artifacts: Path) -> None:
        assert client.get("/api/runs/r1/metrics", params={"limit": 1}).json()["count"] == 1

    def test_a_malformed_verdict_is_served_verbatim(self, client, artifacts: Path) -> None:
        """Artifacts are files. Nothing here parses one, so nothing here rejects one."""
        (artifacts / "verdict.json").write_text("{ truncated")
        response = client.get("/api/runs/r1/artifacts/verdict.json")
        assert response.status_code == 200
        assert response.text == "{ truncated"

    def test_a_run_without_the_file_is_404(self, client, add_run, tmp_path: Path) -> None:
        empty = tmp_path / "empty"
        empty.mkdir()
        add_run("bare", run_dir=empty)
        assert client.get("/api/runs/bare/artifacts/verdict.json").status_code == 404
        assert client.get("/api/runs/bare/artifacts/manifest.json").status_code == 404
        assert client.get("/api/runs/bare/metrics").status_code == 404
