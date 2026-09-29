"""Resolving the commit a build was made from."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

from gauntlet_sdk.reporting import manifest
from gauntlet_sdk.reporting.manifest import GitState

import gauntlet


class TestGitSha:
    def test_the_environment_wins_over_everything_else(self, monkeypatch):
        monkeypatch.setenv("GAUNTLET_GIT_SHA", "from-env")

        assert gauntlet.git_sha() == "from-env"

    def test_a_baked_in_commit_is_used_when_present(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_GIT_SHA", raising=False)
        module = types.ModuleType("gauntlet._build_info")
        module.GIT_SHA = "baked-in"
        monkeypatch.setitem(sys.modules, "gauntlet._build_info", module)

        assert gauntlet.git_sha() == "baked-in"

    def test_a_live_checkout_falls_back_to_git(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_GIT_SHA", raising=False)
        # A None entry makes the import fail as it does where no build has
        # written the module, whether or not this checkout has one on disk.
        monkeypatch.setitem(sys.modules, "gauntlet._build_info", None)

        # This checkout has a real .git, so the fallback finds a real commit
        # rather than needing one faked up.
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(gauntlet.__file__).parent,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        assert gauntlet.git_sha() == head

    def test_an_empty_baked_in_commit_falls_back_to_git(self, monkeypatch):
        monkeypatch.delenv("GAUNTLET_GIT_SHA", raising=False)
        module = types.ModuleType("gauntlet._build_info")
        module.GIT_SHA = ""
        monkeypatch.setitem(sys.modules, "gauntlet._build_info", module)
        monkeypatch.setattr(manifest, "git_state", lambda cwd: GitState(sha="from-git"))

        assert gauntlet.git_sha() == "from-git"
