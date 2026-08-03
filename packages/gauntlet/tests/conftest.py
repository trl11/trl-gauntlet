"""Fixtures building throwaway suites on disk.

Tests construct real suite directories rather than mocking discovery, because
discovery, launching, and conformance are all about what is actually on disk.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from gauntlet.suites import load_suite


@pytest.fixture
def suite_root(tmp_path: Path) -> Path:
    root = tmp_path / "suites"
    root.mkdir()
    return root


@pytest.fixture
def make_suite(suite_root: Path):
    """Write a suite directory and return the loaded result."""

    def _make(key: str = "demo", *, script: str | None = None, **manifest_overrides):
        directory = suite_root / key
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
