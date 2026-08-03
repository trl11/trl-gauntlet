"""Loading ``suite.yaml`` from disk.

The model itself lives in :mod:`gauntlet_suite.contract` so the SDK and the app
share one definition. This module handles the filesystem side: finding the
file, reading it, and pairing the result with where it came from.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from gauntlet_suite.contract import CONTRACT_VERSION, Artifact, SuiteManifest
from pydantic import BaseModel, ConfigDict, ValidationError


class ManifestError(ValueError):
    """A suite.yaml is missing, unreadable, or does not match the contract."""


class LoadedSuite(BaseModel):
    """A validated manifest plus where it was found."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    manifest: SuiteManifest
    directory: Path
    manifest_path: Path

    @property
    def key(self) -> str:
        return self.manifest.key

    @property
    def profiles_dir(self) -> Path:
        return (self.directory / self.manifest.profiles).resolve()

    @property
    def workdir(self) -> Path:
        return (self.directory / self.manifest.exec.workdir).resolve()

    def produces(self, artifact: Artifact) -> bool:
        """Does this suite declare that it writes the given artifact."""
        return artifact in self.manifest.produces

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the REST API."""
        payload = self.manifest.model_dump(mode="json")
        payload["directory"] = str(self.directory)
        return payload


def load_manifest(path: Path) -> SuiteManifest:
    """Read and validate one ``suite.yaml``.

    Validation errors are re-raised with the file path attached, because the
    message goes straight to whoever is writing the suite.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ManifestError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ManifestError(f"{path}: expected a mapping at the top level")

    version = raw.get("apiVersion")
    if version != CONTRACT_VERSION:
        raise ManifestError(
            f"{path}: apiVersion {version!r} is not supported (this Gauntlet speaks {CONTRACT_VERSION})"
        )
    try:
        return SuiteManifest.model_validate(raw)
    except ValidationError as exc:
        raise ManifestError(f"{path}:\n{_render(exc)}") from exc


def load_suite(directory: Path) -> LoadedSuite:
    """Load the suite rooted at ``directory``."""
    manifest_path = directory / "suite.yaml"
    if not manifest_path.is_file():
        raise ManifestError(f"{directory}: no suite.yaml")
    return LoadedSuite(
        manifest=load_manifest(manifest_path),
        directory=directory.resolve(),
        manifest_path=manifest_path.resolve(),
    )


def _render(exc: ValidationError) -> str:
    """Format validation errors as one readable line each."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
