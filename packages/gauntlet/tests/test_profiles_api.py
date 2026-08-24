"""Profile editing: diff against disk, duplicate, and delete."""

from __future__ import annotations


class TestProfileDiff:
    def test_reports_the_changed_lines(self, client) -> None:
        body = client.post(
            "/api/suites/alpha/profiles/smoke.yaml/diff",
            json={"body": "description: slow\niterations: 2\n"},
        )
        assert body.status_code == 200
        diff = body.json()["diff"]
        assert "-description: fast" in diff
        assert "+description: slow" in diff

    def test_identical_content_diffs_to_nothing(self, client) -> None:
        current = client.get("/api/suites/alpha/profiles/smoke.yaml").json()["body"]
        response = client.post("/api/suites/alpha/profiles/smoke.yaml/diff", json={"body": current})
        assert response.json()["diff"] == ""

    def test_name_without_the_extension_works(self, client) -> None:
        assert client.post("/api/suites/alpha/profiles/smoke/diff", json={"body": ""}).status_code == 200

    def test_unknown_profile_is_404(self, client) -> None:
        assert client.post("/api/suites/alpha/profiles/nope/diff", json={"body": ""}).status_code == 404

    def test_unknown_suite_is_404(self, client) -> None:
        assert client.post("/api/suites/nope/profiles/smoke/diff", json={"body": ""}).status_code == 404


class TestProfileDuplicate:
    def test_copies_into_the_user_directory(self, client) -> None:
        response = client.post("/api/suites/alpha/profiles/smoke.yaml/duplicate", json={"name": "mine"})
        assert response.status_code == 201
        assert response.json()["name"] == "mine.yaml"
        assert response.json()["user_authored"] is True
        assert client.get("/api/suites/alpha/profiles/mine.yaml").json()["body"].startswith("description: fast")

    def test_a_taken_name_is_409(self, client) -> None:
        client.post("/api/suites/alpha/profiles/smoke.yaml/duplicate", json={"name": "mine"})
        response = client.post("/api/suites/alpha/profiles/smoke.yaml/duplicate", json={"name": "mine"})
        assert response.status_code == 409

    def test_the_suite_own_name_is_409(self, client) -> None:
        response = client.post("/api/suites/alpha/profiles/smoke.yaml/duplicate", json={"name": "smoke"})
        assert response.status_code == 409

    def test_a_path_as_a_name_is_422(self, client) -> None:
        response = client.post("/api/suites/alpha/profiles/smoke.yaml/duplicate", json={"name": "../escape"})
        assert response.status_code == 422


class TestProfileBodies:
    def test_a_diff_without_content_is_422(self, client) -> None:
        assert client.post("/api/suites/alpha/profiles/smoke.yaml/diff", json={}).status_code == 422

    def test_an_unexpected_diff_key_is_422(self, client) -> None:
        response = client.post("/api/suites/alpha/profiles/smoke.yaml/diff", json={"body": "", "force": True})
        assert response.status_code == 422

    def test_a_duplicate_without_a_name_is_422(self, client) -> None:
        assert client.post("/api/suites/alpha/profiles/smoke.yaml/duplicate", json={}).status_code == 422

    def test_saving_a_non_string_body_is_422(self, client) -> None:
        assert client.put("/api/suites/alpha/profiles/mine", json={"body": 12}).status_code == 422

    def test_saving_to_a_path_is_422(self, client) -> None:
        # A forward slash never reaches the handler: it does not match `{name}`.
        assert client.put("/api/suites/alpha/profiles/..%5Cescape", json={"body": ""}).status_code == 422
        # A forward slash never reaches the `{name}` handler. The SPA catch-all
        # takes the path for GET alone, so a PUT is refused the method.
        assert client.put("/api/suites/alpha/profiles/sub/mine", json={"body": ""}).status_code == 405

    def test_saving_to_a_dotfile_is_422(self, client) -> None:
        assert client.put("/api/suites/alpha/profiles/.hidden", json={"body": ""}).status_code == 422

    def test_saving_to_an_unknown_suite_is_404(self, client) -> None:
        assert client.put("/api/suites/nope/profiles/mine", json={"body": ""}).status_code == 404

    def test_a_yml_name_keeps_its_own_extension(self, client) -> None:
        assert client.put("/api/suites/alpha/profiles/mine.yml", json={"body": "a: 1\n"}).json()["name"] == "mine.yml"


class TestProfileDelete:
    def test_removes_an_operator_profile(self, client) -> None:
        client.put("/api/suites/alpha/profiles/mine", json={"body": "description: mine\n"})
        assert client.delete("/api/suites/alpha/profiles/mine.yaml").json() == {"id": "mine.yaml", "deleted": True}
        assert client.get("/api/suites/alpha/profiles/mine.yaml").status_code == 404

    def test_a_suite_own_profile_is_409(self, client) -> None:
        assert client.delete("/api/suites/alpha/profiles/smoke.yaml").status_code == 409
        assert client.get("/api/suites/alpha/profiles/smoke.yaml").status_code == 200

    def test_unknown_profile_is_404(self, client) -> None:
        assert client.delete("/api/suites/alpha/profiles/nope").status_code == 404
