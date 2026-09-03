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

# The profile offered first, whatever it sorts as. It is the one that runs
# without hardware and proves the suite executes, so it is what an operator
# reaches for before anything else.
_FIRST_PROFILE = "smoke"

# And the one offered last. It has no duration of its own and samples until
# the operator stops it, so it is the profile least often wanted and the one
# worst to start by accident.
_LAST_PROFILE = "continuous"
_MAX_DEPTH = 3


@dataclass
class ProfileInfo:
    """One profile file offered for a suite."""

    name: str
    path: Path
    description: str = ""
    user_authored: bool = False

    @property
    def label(self) -> str:
        """The filename as something to show an operator.

        Derived rather than declared, so every profile has one and a suite
        cannot ship a name the UI has to fall back from.
        """
        stem = Path(self.name).stem.replace("_", " ").replace("-", " ")
        return " ".join(word[:1].upper() + word[1:] for word in stem.split())

    def to_dict(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "label": self.label,
            "name": self.name,
            "path": str(self.path),
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
        # A directory the process cannot read is skipped rather than raised:
        # `is_file()` propagates a permission error, unlike a missing path.
        try:
            if manifest.is_file():
                found.append(manifest)
                return
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
    return [by_name[name] for name in sorted(by_name, key=_profile_order)]


def _profile_order(name: str) -> tuple[int, str]:
    """Sort key putting smoke first, continuous last, and the rest between."""
    stem = Path(name).stem
    if stem == _FIRST_PROFILE:
        return (0, name)
    if stem == _LAST_PROFILE:
        return (2, name)
    return (1, name)


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
