"""Suite catalog endpoints: rescan, detail, and the generated profile schema."""

from __future__ import annotations

import textwrap

import pytest


def _write_schema_command(directory, script: str) -> None:
    """Give a suite an executable ``schema.sh`` for exec.profile_schema_command."""
    path = directory / "schema.sh"
    path.write_text(textwrap.dedent(script))
    path.chmod(0o755)


@pytest.fixture
def schema_suite(client, make_suite, suite_root):
    """A second suite declaring a profile schema command, already discovered."""

    def _make(script: str, command=None):
        make_suite(
            "beta",
            exec={
                "command": ["./run.sh"],
                "args": {"run_dir": "--run-dir", "profile": "--profile"},
                "profile_schema_command": command or ["./schema.sh"],
            },
        )
        _write_schema_command(suite_root / "beta", script)
        client.post("/api/suites/rescan")

    return _make


def _keys(client) -> list[str]:
    return [suite["key"] for suite in client.get("/api/suites").json()["suites"]]


class TestRescan:
    def test_a_suite_added_after_startup_is_found(self, client, make_suite):
        assert _keys(client) == ["alpha"]

        make_suite("beta")
        response = client.post("/api/suites/rescan")

        assert response.status_code == 200
        assert response.json()["count"] == 2
        assert response.json()["errors"] == []
        assert _keys(client) == ["alpha", "beta"]

    def test_a_suite_removed_after_startup_is_dropped(self, client, suite_root):
        (suite_root / "alpha" / "suite.yaml").unlink()

        assert client.post("/api/suites/rescan").json()["count"] == 0
        assert client.get("/api/suites/alpha").status_code == 404

    def test_a_broken_manifest_is_reported_without_hiding_the_others(self, client, suite_root):
        broken = suite_root / "broken"
        broken.mkdir()
        (broken / "suite.yaml").write_text("apiVersion: 1\nkey: 'Not Valid'\n")

        body = client.post("/api/suites/rescan").json()

        assert body["count"] == 1
        assert len(body["errors"]) == 1


class TestSuiteDetail:
    def test_one_suite_is_returned_with_its_profiles(self, client):
        body = client.get("/api/suites/alpha").json()

        assert body["key"] == "alpha"
        assert [p["name"] for p in body["profiles_available"]] == ["quick.yaml"]

    def test_an_operator_profile_is_listed_alongside_the_shipped_one(self, client):
        client.put("/api/suites/alpha/profiles/mine", json={"body": "iterations: 1\n"})

        names = [p["name"] for p in client.get("/api/suites/alpha").json()["profiles_available"]]

        assert sorted(names) == ["mine.yaml", "quick.yaml"]

    def test_an_unknown_suite_is_404(self, client):
        assert client.get("/api/suites/nope").status_code == 404


class TestProfileSchema:
    def test_the_command_output_is_served_as_the_schema(self, client, schema_suite):
        schema_suite(
            """\
            #!/usr/bin/env bash
            echo '{"title": "Profile", "properties": {"iterations": {"type": "integer"}}}'
            """
        )

        body = client.get("/api/suites/beta/profile-schema").json()

        assert body["title"] == "Profile"
        assert "iterations" in body["properties"]

    def test_the_command_runs_with_the_suite_on_its_path(self, client, schema_suite):
        schema_suite(
            """\
            #!/usr/bin/env bash
            echo "{\\"cwd\\": \\"$PWD\\", \\"suite\\": \\"$GAUNTLET_SUITE_DIR\\"}"
            """
        )

        body = client.get("/api/suites/beta/profile-schema").json()

        assert body["cwd"].endswith("/beta")
        assert body["suite"].endswith("/beta")

    def test_a_command_that_fails_is_502_with_its_stderr(self, client, schema_suite):
        schema_suite(
            """\
            #!/usr/bin/env bash
            echo "no profile model" >&2
            exit 3
            """
        )

        response = client.get("/api/suites/beta/profile-schema")

        assert response.status_code == 502
        assert "exited 3" in response.json()["detail"]
        assert "no profile model" in response.json()["detail"]

    def test_a_command_that_prints_something_else_is_502(self, client, schema_suite):
        schema_suite(
            """\
            #!/usr/bin/env bash
            echo "not json"
            """
        )

        response = client.get("/api/suites/beta/profile-schema")

        assert response.status_code == 502
        assert "did not print JSON" in response.json()["detail"]

    def test_a_command_printing_json_that_is_not_an_object_is_502(self, client, schema_suite):
        schema_suite(
            """\
            #!/usr/bin/env bash
            echo '[1, 2]'
            """
        )

        assert client.get("/api/suites/beta/profile-schema").status_code == 502

    def test_a_command_that_cannot_be_spawned_is_502(self, client, schema_suite):
        schema_suite("", command=["./does-not-exist.sh"])

        response = client.get("/api/suites/beta/profile-schema")

        assert response.status_code == 502
        assert "profile schema command failed" in response.json()["detail"]

    def test_a_suite_that_declares_no_command_is_404(self, client):
        assert client.get("/api/suites/alpha/profile-schema").status_code == 404


class TestUnreadableFiles:
    """Filesystem failures answer 500 rather than propagating."""

    def test_reading_a_profile_that_cannot_be_opened_is_500(self, client, suite_root):
        (suite_root / "alpha" / "profiles" / "quick.yaml").chmod(0o000)

        try:
            assert client.get("/api/suites/alpha/profiles/quick.yaml").status_code == 500
        finally:
            (suite_root / "alpha" / "profiles" / "quick.yaml").chmod(0o644)

    def test_diffing_a_profile_that_cannot_be_opened_is_500(self, client, suite_root):
        (suite_root / "alpha" / "profiles" / "quick.yaml").chmod(0o000)

        try:
            response = client.post("/api/suites/alpha/profiles/quick.yaml/diff", json={"content": ""})
            assert response.status_code == 500
        finally:
            (suite_root / "alpha" / "profiles" / "quick.yaml").chmod(0o644)

    def test_duplicating_a_profile_that_cannot_be_opened_is_500(self, client, suite_root):
        (suite_root / "alpha" / "profiles" / "quick.yaml").chmod(0o000)

        try:
            response = client.post("/api/suites/alpha/profiles/quick.yaml/duplicate", json={"name": "copy"})
            assert response.status_code == 500
        finally:
            (suite_root / "alpha" / "profiles" / "quick.yaml").chmod(0o644)

    def test_saving_into_a_directory_that_cannot_be_written_is_500(self, client, settings):
        directory = settings.profiles_dir / "alpha"
        directory.mkdir(parents=True)
        directory.chmod(0o500)

        try:
            response = client.put("/api/suites/alpha/profiles/mine", json={"body": "iterations: 1\n"})
            assert response.status_code == 500
        finally:
            directory.chmod(0o700)

    def test_deleting_a_profile_that_cannot_be_removed_is_500(self, client, settings):
        client.put("/api/suites/alpha/profiles/mine", json={"body": "iterations: 1\n"})
        directory = settings.profiles_dir / "alpha"
        directory.chmod(0o500)

        try:
            assert client.delete("/api/suites/alpha/profiles/mine.yaml").status_code == 500
        finally:
            directory.chmod(0o700)
