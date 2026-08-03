"""Finds suites on disk and lists their profiles.

Read-only: no subprocesses, no side effects. Callers cache the result and
refresh it on demand, so a suite added while Gauntlet is running appears after
a rescan rather than requiring a restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from gauntlet.suites.manifest import LoadedSuite, ManifestError, load_suite

log = logging.getLogger("gauntlet.suites")

_PROFILE_SUFFIXES = (".yaml", ".yml")
_MAX_DEPTH = 3


@dataclass
class ProfileInfo:
    """One profile file offered for a suite."""

    name: str
    path: Path
    description: str = ""
    user_authored: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "path": str(self.path),
            "description": self.description,
            "user_authored": self.user_authored,
        }


@dataclass
class SuiteCatalog:
    """Every suite Gauntlet found, plus what went wrong for those it did not."""

    suites: dict[str, LoadedSuite] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def get(self, key: str) -> LoadedSuite | None:
        return self.suites.get(key)

    def to_dict(self, profiles: dict[str, list[ProfileInfo]] | None = None) -> dict[str, Any]:
        entries = []
        for key in sorted(self.suites):
            payload = self.suites[key].to_dict()
            if profiles is not None:
                payload["profiles_available"] = [p.to_dict() for p in profiles.get(key, [])]
            entries.append(payload)
        return {"suites": entries, "errors": list(self.errors)}


def discover_suites(roots: list[Path]) -> SuiteCatalog:
    """Walk the roots and load every ``suite.yaml`` found.

    Malformed manifests are collected into :attr:`SuiteCatalog.errors` rather
    than raised. On a key collision the earlier root wins.
    """
    catalog = SuiteCatalog()
    for root in roots:
        if not root.is_dir():
            continue
        for manifest_path in _find_manifests(root):
            try:
                suite = load_suite(manifest_path.parent)
            except ManifestError as exc:
                catalog.errors.append(str(exc))
                log.warning("skipping suite at %s: %s", manifest_path.parent, exc)
                continue
            existing = catalog.suites.get(suite.key)
            if existing is not None:
                catalog.errors.append(
                    f"duplicate suite key {suite.key!r}: keeping {existing.directory}, ignoring {suite.directory}"
                )
                continue
            catalog.suites[suite.key] = suite
    return catalog


def _find_manifests(root: Path) -> list[Path]:
    """Collect ``suite.yaml`` files without descending into a suite."""
    found: list[Path] = []

    def _walk(directory: Path, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        manifest = directory / "suite.yaml"
        if manifest.is_file():
            found.append(manifest)
            return
        try:
            children = sorted(p for p in directory.iterdir() if p.is_dir())
        except OSError:
            return
        for child in children:
            if child.name.startswith((".", "_")) or child.name in {"__pycache__", "node_modules"}:
                continue
            _walk(child, depth + 1)

    _walk(root, 0)
    return found


def list_profiles(suite: LoadedSuite, user_profiles_dir: Path | None = None) -> list[ProfileInfo]:
    """List the profiles offered for a suite.

    Combines the suite's own profiles with any under
    ``<user_profiles_dir>/<suite key>/``. A user profile shadows a shipped one
    of the same filename.
    """
    by_name: dict[str, ProfileInfo] = {}
    for path in _profile_files(suite.profiles_dir):
        by_name[path.name] = ProfileInfo(
            name=path.name,
            path=path,
            description=_describe(path),
        )
    if user_profiles_dir is not None:
        for path in _profile_files(user_profiles_dir / suite.key):
            by_name[path.name] = ProfileInfo(
                name=path.name,
                path=path,
                description=_describe(path),
                user_authored=True,
            )
    return [by_name[name] for name in sorted(by_name)]


def resolve_profile(suite: LoadedSuite, name: str, user_profiles_dir: Path | None = None) -> Path | None:
    """Resolve a profile name to a path, preferring the operator's copy."""
    if "/" in name or "\\" in name or name.startswith("."):
        return None
    for info in list_profiles(suite, user_profiles_dir):
        if info.name == name or Path(info.name).stem == name:
            return info.path
    return None


def _profile_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.iterdir() if p.is_file() and p.suffix in _PROFILE_SUFFIXES)


def _describe(path: Path) -> str:
    """Read the ``description`` field from a profile for the listing.

    Only this key is read; profiles are typed by each suite's own model.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError):
        return ""
    if isinstance(raw, dict):
        value = raw.get("description")
        if isinstance(value, str):
            return value.strip()
    return ""
