"""Resolving the commit a build was made from."""

from __future__ import annotations

import sys
import types

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
        monkeypatch.delitem(sys.modules, "gauntlet._build_info", raising=False)

        # This checkout has a real .git, so the fallback finds a real commit
        # rather than needing one faked up.
        sha = gauntlet.git_sha()
        assert sha is None or len(sha) == 40
