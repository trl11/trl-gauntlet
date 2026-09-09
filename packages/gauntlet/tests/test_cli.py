"""The ``gauntlet`` command, driven through :func:`gauntlet.cli.main`."""

from __future__ import annotations

import json
import runpy
import sys
from pathlib import Path

import pytest

from gauntlet import cli


@pytest.fixture(autouse=True)
def isolated_data_dir(monkeypatch, tmp_path: Path):
    """Keep ``load_settings`` away from the developer's own config and output.

    The campaign root is pointed at an empty directory rather than unset: the
    default is ``./campaigns``, so a test run from the repository would
    otherwise discover the built-in campaigns and every suite they carry.
    """
    empty = tmp_path / "no-campaigns"
    empty.mkdir()
    monkeypatch.setenv("GAUNTLET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("GAUNTLET_CAMPAIGN_PATH", str(empty))
    monkeypatch.delenv("GAUNTLET_SUITE_PATH", raising=False)


class TestList:
    def test_prints_a_row_per_suite(self, capsys, make_suite, suite_root: Path) -> None:
        make_suite("alpha")
        make_suite("beta")

        assert cli.main(["list", "--suites", str(suite_root)]) == 0
        out = capsys.readouterr().out
        assert "alpha" in out
        assert "beta" in out

    def test_json_carries_the_whole_catalog(self, capsys, make_suite, suite_root: Path) -> None:
        make_suite("alpha")

        assert cli.main(["list", "--suites", str(suite_root), "--json"]) == 0
        body = json.loads(capsys.readouterr().out)
        assert [entry["key"] for entry in body["suites"]] == ["alpha"]

    def test_says_so_when_nothing_was_found(self, capsys, suite_root: Path) -> None:
        assert cli.main(["list", "--suites", str(suite_root)]) == 0
        assert "No suites found" in capsys.readouterr().out

    def test_a_broken_manifest_exits_one(self, capsys, suite_root: Path) -> None:
        broken = suite_root / "broken"
        broken.mkdir()
        (broken / "suite.yaml").write_text("apiVersion: 1\nkey: broken\n")

        assert cli.main(["list", "--suites", str(suite_root)]) == 1
        assert "error:" in capsys.readouterr().err

    def test_json_also_exits_one_on_a_broken_manifest(self, capsys, suite_root: Path) -> None:
        broken = suite_root / "broken"
        broken.mkdir()
        (broken / "suite.yaml").write_text("apiVersion: 1\nkey: broken\n")

        assert cli.main(["list", "--suites", str(suite_root), "--json"]) == 1
        assert json.loads(capsys.readouterr().out)["errors"]


class TestVerify:
    def test_a_named_directory_passes(self, capsys, make_suite) -> None:
        suite = make_suite("alpha")

        assert cli.main(["verify", str(suite.directory)]) == 0
        out = capsys.readouterr().out
        assert "alpha  [PASS]" in out
        assert "ok   suite.yaml is valid" in out

    def test_every_discovered_suite_is_checked(self, capsys, make_suite, suite_root: Path) -> None:
        make_suite("alpha")
        make_suite("beta")

        assert cli.main(["verify", "--suites", str(suite_root)]) == 0
        assert capsys.readouterr().out.count("[PASS]") == 2

    def test_json_reports_every_check(self, capsys, make_suite) -> None:
        suite = make_suite("alpha")

        assert cli.main(["verify", str(suite.directory), "--json"]) == 0
        reports = json.loads(capsys.readouterr().out)
        assert reports[0]["passed"] is True
        assert reports[0]["checks"]

    def test_a_failing_suite_exits_one_and_prints_the_detail(self, capsys, make_suite) -> None:
        suite = make_suite("alpha")
        (suite.directory / "run.sh").unlink()

        assert cli.main(["verify", str(suite.directory)]) == 1
        out = capsys.readouterr().out
        assert "[FAIL]" in out
        assert "FAIL" in out

    def test_nothing_to_verify_exits_one(self, capsys, suite_root: Path) -> None:
        assert cli.main(["verify", "--suites", str(suite_root)]) == 1
        assert "no suites to verify" in capsys.readouterr().err

    def test_run_executes_the_conformance_profile(self, capsys, make_suite) -> None:
        suite = make_suite("alpha")

        assert cli.main(["verify", str(suite.directory), "--run"]) == 0
        assert "[PASS]" in capsys.readouterr().out


class TestSchema:
    def test_no_name_lists_them(self, capsys) -> None:
        assert cli.main(["schema"]) == 0
        assert "verdict" in capsys.readouterr().out.split()

    def test_a_name_prints_json_schema(self, capsys) -> None:
        assert cli.main(["schema", "suite"]) == 0
        schema = json.loads(capsys.readouterr().out)
        assert schema["type"] == "object"
        assert "key" in schema["properties"]

    def test_an_unknown_name_is_rejected_by_the_parser(self) -> None:
        with pytest.raises(SystemExit):
            cli.main(["schema", "nope"])


class TestNewSuite:
    def test_renders_into_the_named_directory(self, capsys, tmp_path: Path) -> None:
        assert cli.main(["new-suite", "my_probe", "--into", str(tmp_path)]) == 0

        assert (tmp_path / "my_probe" / "suite.yaml").is_file()
        assert (tmp_path / "my_probe" / "suite" / "runner.py").is_file()
        out = capsys.readouterr().out
        assert "template: python" in out
        assert "suite/runner.py" in out

    def test_the_shell_template_names_its_own_entry_point(self, capsys, tmp_path: Path) -> None:
        assert cli.main(["new-suite", "shell_probe", "--template", "shell", "--into", str(tmp_path)]) == 0

        assert (tmp_path / "shell_probe" / "run.sh").is_file()
        assert "run.sh" in capsys.readouterr().out

    def test_defaults_to_a_suites_directory_under_the_cwd(self, capsys, monkeypatch, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)

        assert cli.main(["new-suite", "here"]) == 0
        assert (tmp_path / "suites" / "here" / "suite.yaml").is_file()
        # The path is printed relative to the working directory when it is inside it.
        assert "Created suites/here" in capsys.readouterr().out

    def test_a_bad_key_exits_two(self, capsys, tmp_path: Path) -> None:
        assert cli.main(["new-suite", "Not-A-Key", "--into", str(tmp_path)]) == 2
        assert "error:" in capsys.readouterr().err

    def test_an_existing_directory_exits_two(self, capsys, tmp_path: Path) -> None:
        cli.main(["new-suite", "twice", "--into", str(tmp_path)])
        assert cli.main(["new-suite", "twice", "--into", str(tmp_path)]) == 2
        assert "error:" in capsys.readouterr().err


class TestTemplates:
    def test_lists_both_templates(self, capsys) -> None:
        assert cli.main(["templates"]) == 0
        assert set(capsys.readouterr().out.split()) == {"python", "shell"}


class TestModuleEntryPoint:
    def test_python_dash_m_gauntlet_runs_the_cli(self, capsys, monkeypatch) -> None:
        monkeypatch.setattr(sys, "argv", ["gauntlet", "templates"])

        with pytest.raises(SystemExit) as exit_code:
            runpy.run_module("gauntlet", run_name="__main__")

        assert exit_code.value.code == 0
        assert "python" in capsys.readouterr().out


class TestServe:
    def test_passes_the_settings_to_uvicorn(self, capsys, monkeypatch, suite_root: Path) -> None:
        import uvicorn

        calls: list[dict[str, object]] = []
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

        assert cli.main(["serve", "--host", "127.0.0.1", "--port", "7999", "--suites", str(suite_root)]) == 0
        assert calls[0]["host"] == "127.0.0.1"
        assert calls[0]["port"] == 7999
        assert "reload" not in calls[0]
        assert "Gauntlet on http://127.0.0.1:7999" in capsys.readouterr().err

    def test_reload_hands_uvicorn_an_import_string(self, monkeypatch, suite_root: Path) -> None:
        import uvicorn

        calls: list[dict[str, object]] = []
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

        assert cli.main(["serve", "--reload", "--suites", str(suite_root)]) == 0
        assert calls[0]["app"] == "gauntlet.app:create_app"
        assert calls[0]["factory"] is True
        assert calls[0]["reload"] is True

    def test_log_level_reaches_the_settings(self, monkeypatch, suite_root: Path) -> None:
        import uvicorn

        calls: list[dict[str, object]] = []
        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: calls.append({"app": app, **kwargs}))

        assert cli.main(["serve", "--log-level", "debug", "--suites", str(suite_root)]) == 0
        assert calls[0]["log_level"] == "debug"

    def test_creates_the_directories_it_writes_to(self, monkeypatch, suite_root: Path, tmp_path: Path) -> None:
        import uvicorn

        monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: None)

        cli.main(["serve", "--suites", str(suite_root)])
        assert (tmp_path / "data" / "runs").is_dir()
        assert (tmp_path / "data" / "profiles").is_dir()


class TestExportImport:
    """Moving a run between two data directories, which is two instances."""

    @pytest.fixture
    def run_on_disk(self, tmp_path: Path) -> Path:
        run_dir = tmp_path / "data" / "runs" / "alpha" / "r1"
        run_dir.mkdir(parents=True)
        (run_dir / "verdict.json").write_text(
            '{"passed": true, "reason": "", "started_at_utc": "2026-01-01T00:00:00Z"}'
        )
        (run_dir / "metrics.jsonl").write_text('{"kind":"iteration","iteration":1,"success":true}\n')
        return run_dir

    def test_a_run_moves_to_another_instance(self, capsys, monkeypatch, run_on_disk, tmp_path: Path) -> None:
        assert cli.main(["export", "r1", "-o", str(tmp_path)]) == 0
        archive = tmp_path / "r1.gauntlet-run.zip"
        assert archive.is_file()

        monkeypatch.setenv("GAUNTLET_DATA_DIR", str(tmp_path / "other"))
        assert cli.main(["import", str(archive)]) == 0
        landed = tmp_path / "other" / "runs" / "alpha" / "r1"
        assert json.loads((landed / "verdict.json").read_text())["passed"] is True
        assert (landed / "metrics.jsonl").is_file()

    def test_output_defaults_to_the_working_directory(self, monkeypatch, run_on_disk, tmp_path: Path) -> None:
        monkeypatch.chdir(tmp_path)

        assert cli.main(["export", "r1"]) == 0
        assert (tmp_path / "r1.gauntlet-run.zip").is_file()

    def test_exporting_an_unknown_run_fails(self, capsys, tmp_path: Path) -> None:
        assert cli.main(["export", "nope", "-o", str(tmp_path)]) == 1
        assert "unknown run" in capsys.readouterr().err

    def test_a_run_already_here_is_refused(self, capsys, run_on_disk, tmp_path: Path) -> None:
        cli.main(["export", "r1", "-o", str(tmp_path)])

        assert cli.main(["import", str(tmp_path / "r1.gauntlet-run.zip")]) == 1
        assert "--overwrite" in capsys.readouterr().err

    def test_overwrite_replaces_it(self, run_on_disk, tmp_path: Path) -> None:
        cli.main(["export", "r1", "-o", str(tmp_path)])

        assert cli.main(["import", str(tmp_path / "r1.gauntlet-run.zip"), "--overwrite"]) == 0

    def test_something_that_is_not_an_export_is_rejected(self, capsys, tmp_path: Path) -> None:
        (tmp_path / "notes.txt").write_text("this is not an archive")

        assert cli.main(["import", str(tmp_path / "notes.txt")]) == 2
        assert "error:" in capsys.readouterr().err


class TestParser:
    def test_no_subcommand_is_rejected(self) -> None:
        with pytest.raises(SystemExit):
            cli.main([])


class TestCampaignRoots:
    """A campaign contributes its suites to what the CLI sees."""

    def test_a_campaign_suite_is_listed(self, capsys, make_campaign, make_suite, campaign_root, suite_root: Path):
        campaign = make_campaign("bench")
        make_suite("beta", root=campaign.suites_dir)

        assert cli.main(["list", "--suites", str(suite_root), "--campaigns", str(campaign_root)]) == 0

        assert "beta" in capsys.readouterr().out

    def test_campaign_and_suite_roots_are_both_read(
        self, capsys, make_campaign, make_suite, campaign_root, suite_root: Path
    ):
        campaign = make_campaign("bench")
        make_suite("alpha")
        make_suite("beta", root=campaign.suites_dir)

        assert cli.main(["list", "--suites", str(suite_root), "--campaigns", str(campaign_root), "--json"]) == 0

        body = json.loads(capsys.readouterr().out)
        assert [entry["key"] for entry in body["suites"]] == ["alpha", "beta"]

    def test_verify_checks_campaign_suites_too(self, capsys, make_campaign, make_suite, campaign_root, suite_root):
        campaign = make_campaign("bench")
        make_suite("beta", root=campaign.suites_dir)

        assert cli.main(["verify", "--suites", str(suite_root), "--campaigns", str(campaign_root)]) == 0

        assert capsys.readouterr().out.count("[PASS]") == 1
