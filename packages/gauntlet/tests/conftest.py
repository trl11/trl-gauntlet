"""Fixtures building throwaway suites on disk.

Tests construct real suite directories rather than mocking discovery, because
discovery, launching, and conformance are all about what is actually on disk.
"""

from __future__ import annotations

import itertools
import textwrap
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from gauntlet import app as app_module
from gauntlet.app import create_app
from gauntlet.campaigns import load_campaign
from gauntlet.config import Settings
from gauntlet.storage import RunRow
from gauntlet.suites import load_suite


def pytest_configure(config: pytest.Config) -> None:
    """Declare the markers, which ``--strict-markers`` requires."""
    config.addinivalue_line("markers", "e2e: drives a real suite through the supervisor; run by `make test-e2e`")


@pytest.fixture
def suite_root(tmp_path: Path) -> Path:
    root = tmp_path / "suites"
    root.mkdir()
    return root


@pytest.fixture
def campaign_root(tmp_path: Path) -> Path:
    root = tmp_path / "campaigns"
    root.mkdir()
    return root


@pytest.fixture
def settings(campaign_root: Path, suite_root: Path, tmp_path: Path) -> Settings:
    """Settings pointed at this test's throwaway roots and data dir.

    The campaign root is empty and inside ``tmp_path``, so a test never picks up
    a campaign from the working directory. Every instrument is simulated and
    none is probed for, so a test reads the same on a bench with hardware
    attached as on one without.
    """
    return Settings(
        host="127.0.0.1",
        port=7100,
        suite_roots=[suite_root],
        campaign_roots=[campaign_root],
        data_dir=tmp_path / "data",
        daq_serial="",
        psu_port="",
        simulated_instruments=["chamber", "daq", "psu"],
    )


@pytest.fixture
def web_dist(monkeypatch, tmp_path: Path) -> Path:
    """Stand in for the built frontend, which may or may not exist in the tree."""
    directory = tmp_path / "web_dist"
    directory.mkdir()
    monkeypatch.setattr(app_module, "_web_dist", lambda: directory)
    return directory


@pytest.fixture
def client(make_suite, settings: Settings, web_dist: Path):
    """An app serving one suite, ``alpha``, and a stand-in for the bundle.

    A wheel ships the bundle, so the app under test mounts the SPA routes
    whether or not this checkout has built the frontend.
    """
    make_suite("alpha")
    (web_dist / "index.html").write_text("<!doctype html><title>Gauntlet</title><div id='root'></div>")
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture
def add_run(client):
    """Write a run straight into the history index.

    Runs are stamped a minute apart in call order, so the most recently added
    is the most recent.
    """
    minute = itertools.count(0)

    def _add(
        run_id: str,
        *,
        ended_at: str | None = None,
        run_dir: Path | str | None = None,
        started_at: str | None = None,
        status: str = "passed",
        suite: str = "alpha",
        unit_serial: str | None = None,
    ) -> RunRow:
        started = started_at or f"2026-01-01T00:{next(minute):02d}:00Z"
        row = RunRow(
            run_id=run_id,
            suite=suite,
            status=status,
            started_at=started,
            run_dir=str(run_dir) if run_dir is not None else f"/tmp/{suite}/{run_id}",
            ended_at=ended_at or started,
            unit_serial=unit_serial,
            verdict="PASS" if status == "passed" else "FAIL",
        )
        client.app.state.runs_index.upsert(row)
        return row

    return _add


@pytest.fixture
def make_campaign(campaign_root: Path):
    """Write a campaign directory, with an empty suite directory, and load it."""

    def _make(key: str = "demo_campaign", *, members: list[dict] | None = None, **manifest_overrides):
        directory = campaign_root / key
        (directory / "suites").mkdir(parents=True)

        manifest = {
            "apiVersion": 1,
            "key": key,
            "title": key.replace("_", " ").title(),
            "suites": "./suites",
            "members": members or [],
        }
        manifest.update(manifest_overrides)
        (directory / "campaign.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
        return load_campaign(directory)

    return _make


@pytest.fixture
def make_suite(suite_root: Path):
    """Write a suite directory and return the loaded result.

    ``root`` places the suite somewhere other than the suite root, so a test can
    put one inside a campaign's suite directory.
    """

    def _make(key: str = "demo", *, root: Path | None = None, script: str | None = None, **manifest_overrides):
        directory = (root or suite_root) / key
        (directory / "profiles").mkdir(parents=True)

        manifest = {
            "apiVersion": 1,
            "key": key,
            "title": key.replace("_", " ").title(),
            "exec": {"command": ["./run.sh"], "args": {"run_dir": "--run-dir", "profile": "--profile"}},
            "profiles": "./profiles",
            "conformance_profile": "quick.yaml",
            "produces": ["metrics", "verdict"],
        }
        manifest.update(manifest_overrides)
        (directory / "suite.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False))
        (directory / "profiles" / "quick.yaml").write_text("description: fast\niterations: 2\n")

        run_sh = directory / "run.sh"
        run_sh.write_text(script or _DEFAULT_SCRIPT)
        run_sh.chmod(0o755)
        return load_suite(directory)

    return _make


_DEFAULT_SCRIPT = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    run_dir="${GAUNTLET_RUN_DIR:-}"
    while [[ $# -gt 0 ]]; do
        case "$1" in
            --run-dir) run_dir="$2"; shift 2 ;;
            *) shift ;;
        esac
    done
    mkdir -p "$run_dir"
    echo '{"kind":"iteration","iteration":1,"timestamp":1,"success":true,"metrics":{"v":1}}' > "$run_dir/metrics.jsonl"
    echo '{"passed": true, "reason": "", "total_iterations": 1}' > "$run_dir/verdict.json"
    echo "done PASS"
    """
)
