"""Keeps every declared version equal to the one in ``VERSION``.

``VERSION`` is the source of truth, but nothing can read it directly: setuptools
refuses a ``dynamic.version`` file outside the package directory, and npm has no
dynamic version at all. So the number is written into each manifest instead, and
``check`` is what stops the copies drifting from it.

    python tools/release/version.py check    # exit 1 on any mismatch
    python tools/release/version.py sync     # rewrite the manifests from VERSION
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
VERSION_FILE = ROOT / "VERSION"

# Every manifest that declares the version, and nothing that only reads it.
PYPROJECTS = (
    ROOT / "packages" / "gauntlet" / "pyproject.toml",
    ROOT / "packages" / "gauntlet-sdk" / "pyproject.toml",
)
PACKAGE_JSONS = (
    ROOT / "targets" / "app" / "package.json",
    ROOT / "frontend" / "package.json",
)

# The version in a pyproject's [project] table, which is the first one in the
# file. A dependency's pin is never at the start of a line.
PYPROJECT_VERSION = re.compile(r'^version = ".*"$', re.MULTILINE)


def declared_version() -> str:
    """The number every manifest is expected to carry."""
    return VERSION_FILE.read_text().strip()


def read_pyproject(path: Path) -> str | None:
    match = PYPROJECT_VERSION.search(path.read_text())
    return match.group(0).split('"')[1] if match else None


def read_package_json(path: Path) -> str | None:
    return json.loads(path.read_text()).get("version")


def write_pyproject(path: Path, version: str) -> None:
    original = path.read_text()
    path.write_text(PYPROJECT_VERSION.sub(f'version = "{version}"', original, count=1))


def write_package_json(path: Path, version: str) -> None:
    # Rewritten as text rather than re-serialised, so that key order, indentation
    # and anything npm wrote survive untouched.
    original = path.read_text()
    current = json.loads(original).get("version")
    path.write_text(original.replace(f'"version": "{current}"', f'"version": "{version}"', 1))


def each_manifest() -> list[tuple[Path, str | None]]:
    """Every manifest with the version it currently declares."""
    return [(path, read_pyproject(path)) for path in PYPROJECTS] + [
        (path, read_package_json(path)) for path in PACKAGE_JSONS
    ]


def check(version: str) -> int:
    wrong = [(path, found) for path, found in each_manifest() if found != version]
    for path, found in wrong:
        print(f"{path.relative_to(ROOT)}: {found}, expected {version}", file=sys.stderr)
    if wrong:
        print("run `make version-sync`", file=sys.stderr)
        return 1
    print(f"version {version}, in {len(each_manifest())} manifests")
    return 0


def sync(version: str) -> int:
    for path, found in each_manifest():
        if found == version:
            continue
        if path.suffix == ".toml":
            write_pyproject(path, version)
        else:
            write_package_json(path, version)
        print(f"{path.relative_to(ROOT)}: {found} -> {version}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["check", "sync"])
    arguments = parser.parse_args()
    version = declared_version()
    return check(version) if arguments.command == "check" else sync(version)


if __name__ == "__main__":
    raise SystemExit(main())
