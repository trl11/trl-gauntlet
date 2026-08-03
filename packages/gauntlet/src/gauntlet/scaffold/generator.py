"""Renders a suite template into a new suite directory.

Templates live in ``templates/<name>/``. Every file is copied with three
placeholders substituted, and a path component named ``__SUITE_KEY__`` is
renamed to the suite key.

| Placeholder | Example for ``my_probe`` |
|---|---|
| ``__SUITE_KEY__`` | ``my_probe`` |
| ``__SUITE_TITLE__`` | ``My Probe`` |
| ``__SUITE_CLASS__`` | ``MyProbe`` |
"""

from __future__ import annotations

import re
import stat
from dataclasses import dataclass
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

SUITE_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

# Copied verbatim; substituting inside them would corrupt the contents.
_BINARY_SUFFIXES = frozenset({".ico", ".jpg", ".png", ".sqlite", ".zip"})

# Never part of a template, but can be present in a source tree or a built
# distribution, and would be rendered as garbage.
_SKIP_NAMES = frozenset({"__pycache__", ".DS_Store", ".pytest_cache", ".ruff_cache"})
_SKIP_SUFFIXES = frozenset({".pyc", ".pyo"})


class ScaffoldError(ValueError):
    """The suite cannot be generated as requested."""


@dataclass(frozen=True)
class Placeholders:
    """The substitutions applied to every template file."""

    key: str
    title: str
    class_name: str

    @classmethod
    def from_key(cls, key: str) -> Placeholders:
        words = key.split("_")
        return cls(
            key=key,
            title=" ".join(word.capitalize() for word in words),
            class_name="".join(word.capitalize() for word in words),
        )

    def as_map(self) -> dict[str, str]:
        return {
            "__SUITE_CLASS__": self.class_name,
            "__SUITE_KEY__": self.key,
            "__SUITE_TITLE__": self.title,
        }


def available_templates() -> list[str]:
    """Names of the templates that can be rendered."""
    if not TEMPLATES_DIR.is_dir():
        return []
    return sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())


def render(key: str, destination_root: Path, *, template: str = "python") -> Path:
    """Generate a suite named ``key`` under ``destination_root``.

    Returns the created directory.
    """
    if not SUITE_KEY_PATTERN.match(key):
        raise ScaffoldError(f"invalid suite name {key!r}: use lower_snake_case starting with a letter")

    template_dir = TEMPLATES_DIR / template
    if not template_dir.is_dir():
        known = ", ".join(available_templates()) or "none"
        raise ScaffoldError(f"unknown template {template!r} (available: {known})")

    destination = destination_root / key
    if destination.exists():
        raise ScaffoldError(f"{destination} already exists")

    placeholders = Placeholders.from_key(key).as_map()
    destination.mkdir(parents=True)
    for source in sorted(template_dir.rglob("*")):
        relative = source.relative_to(template_dir)
        if _skip(relative):
            continue
        target = destination / _substitute(str(relative), placeholders)
        if source.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        _copy(source, target, placeholders)
    return destination


def _skip(relative: Path) -> bool:
    """Is this a build artifact rather than template content."""
    return bool(_SKIP_NAMES.intersection(relative.parts)) or relative.suffix in _SKIP_SUFFIXES


def _copy(source: Path, target: Path, placeholders: dict[str, str]) -> None:
    if source.suffix in _BINARY_SUFFIXES:
        target.write_bytes(source.read_bytes())
    else:
        target.write_text(_substitute(source.read_text(), placeholders))
    if source.stat().st_mode & stat.S_IXUSR:
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _substitute(text: str, placeholders: dict[str, str]) -> str:
    for placeholder, value in placeholders.items():
        text = text.replace(placeholder, value)
    return text
