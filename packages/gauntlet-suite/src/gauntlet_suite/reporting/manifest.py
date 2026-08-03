"""Writer for ``manifest.json``.

Records the command line, working directory, git state, and versions for one
run.
"""

from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class GitState:
    sha: str | None = None
    branch: str | None = None
    dirty: bool = False


@dataclass
class Manifest:
    """Provenance record written once at the start of a run."""

    suite: str
    run_id: str
    started_at_utc: str
    hostname: str = ""
    platform: str = ""
    python_version: str = ""
    cwd: str = ""
    command_line: list[str] = field(default_factory=list)
    repo_sha: str | None = None
    repo_branch: str | None = None
    repo_dirty: bool = False
    target: str | None = None
    unit_serial: str | None = None
    profile_path: str | None = None
    profile_summary: dict[str, str] = field(default_factory=dict)
    hardware: dict[str, dict[str, str]] = field(default_factory=dict)
    versions: dict[str, str] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)


def git_state(cwd: Path | None = None) -> GitState:
    """Best-effort git description of the working tree, empty when absent."""

    def _run(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=str(cwd) if cwd else None,
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout.strip() if out.returncode == 0 else None

    sha = _run("rev-parse", "HEAD")
    if sha is None:
        return GitState()
    return GitState(
        sha=sha,
        branch=_run("rev-parse", "--abbrev-ref", "HEAD"),
        dirty=bool(_run("status", "--porcelain")),
    )


def build_manifest(
    *,
    suite: str,
    run_id: str,
    started_at_utc: str | None = None,
    target: str | None = None,
    unit_serial: str | None = None,
    profile_path: Path | None = None,
    profile_summary: dict[str, str] | None = None,
    hardware: dict[str, dict[str, str]] | None = None,
    versions: dict[str, str] | None = None,
    extra_env: dict[str, str] | None = None,
) -> Manifest:
    """Sample the current process and build a manifest from it."""
    git = git_state()
    env = {k: v for k, v in os.environ.items() if k.startswith("GAUNTLET_") and v}
    if extra_env:
        env.update(extra_env)
    return Manifest(
        suite=suite,
        run_id=run_id,
        started_at_utc=started_at_utc or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        hostname=socket.gethostname(),
        platform=platform.platform(),
        python_version=sys.version.split()[0],
        cwd=os.getcwd(),
        command_line=list(sys.argv),
        repo_sha=git.sha,
        repo_branch=git.branch,
        repo_dirty=git.dirty,
        target=target,
        unit_serial=unit_serial,
        profile_path=str(profile_path) if profile_path else None,
        profile_summary=profile_summary or {},
        hardware=hardware or {},
        versions=versions or {},
        env=env,
    )


def write_manifest(path: Path, manifest: Manifest) -> None:
    """Serialize a manifest to disk."""
    payload: dict[str, Any] = asdict(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
