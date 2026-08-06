"""Loading ``campaign.yaml`` from disk.

A campaign groups the suites of one test programme and records how each is
meant to be run. Suites never see it: it is an operator-facing arrangement of
suites, not part of the suite contract, so the model lives here rather than in
:mod:`gauntlet_sdk.contract`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

CAMPAIGN_VERSION = 1

MANIFEST_NAME = "campaign.yaml"


class CampaignError(ValueError):
    """A campaign.yaml is missing, unreadable, or does not match the schema."""


class CampaignMember(BaseModel):
    """How one suite is meant to be run in this campaign.

    Membership comes from the campaign's suite directory, not from this list. A
    member entry configures a suite found there; a suite with no entry is still
    in the campaign, just unconfigured. That way the directory can gain and lose
    suites without the manifest going stale.
    """

    model_config = ConfigDict(extra="forbid")

    suite: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    component: str = Field(default="", max_length=120, description="Manufacturer part number under test.")
    test_vehicle: str = Field(default="", max_length=120, description="Board or module carrying the component.")
    host: str = Field(default="", max_length=120, description="What drives the test vehicle.")
    fixture: str = Field(default="", max_length=40, description="Position in the beam, from the test plan.")
    profile: str = Field(default="", max_length=120, description="Profile offered by default when starting a run.")
    target: str = Field(default="", max_length=200)
    unit_serial: str = Field(default="", max_length=120)
    overrides: dict[str, Any] = Field(
        default_factory=dict,
        description="Override values offered by default. Validated against the suite's manifest when a run starts.",
    )
    notes: str = Field(default="", max_length=1000)


class CampaignManifest(BaseModel):
    """A ``campaign.yaml``. The entire registration surface for a campaign."""

    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal[1]
    key: str = Field(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)
    title: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=1000)
    suites: str = Field(
        default="./suites",
        description="Directory holding this campaign's suites, added to the suite discovery roots.",
    )
    members: list[CampaignMember] = Field(default_factory=list)

    def member(self, suite: str) -> CampaignMember | None:
        """Look up a member entry by suite key."""
        return next((m for m in self.members if m.suite == suite), None)


class LoadedCampaign(BaseModel):
    """A validated manifest plus where it was found."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    manifest: CampaignManifest
    directory: Path
    manifest_path: Path

    @property
    def key(self) -> str:
        return self.manifest.key

    @property
    def suites_dir(self) -> Path:
        """Where this campaign's suites live."""
        return (self.directory / self.manifest.suites).resolve()

    def owns(self, suite_directory: Path) -> bool:
        """Does this campaign's suite directory contain the given suite."""
        try:
            suite_directory.resolve().relative_to(self.suites_dir)
        except ValueError:
            return False
        return True

    def to_dict(self) -> dict[str, Any]:
        """Serialize for the REST API."""
        payload = self.manifest.model_dump(mode="json")
        payload["directory"] = str(self.directory)
        payload["suites_dir"] = str(self.suites_dir)
        return payload


def load_manifest(path: Path) -> CampaignManifest:
    """Read and validate one ``campaign.yaml``.

    Validation errors are re-raised with the file path attached, because the
    message goes straight to whoever is writing the campaign.
    """
    try:
        raw = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise CampaignError(f"cannot read {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise CampaignError(f"invalid YAML in {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise CampaignError(f"{path}: expected a mapping at the top level")

    version = raw.get("apiVersion")
    if version != CAMPAIGN_VERSION:
        raise CampaignError(
            f"{path}: apiVersion {version!r} is not supported (this Gauntlet speaks {CAMPAIGN_VERSION})"
        )
    try:
        return CampaignManifest.model_validate(raw)
    except ValidationError as exc:
        raise CampaignError(f"{path}:\n{_render(exc)}") from exc


def load_campaign(directory: Path) -> LoadedCampaign:
    """Load the campaign rooted at ``directory``."""
    manifest_path = directory / MANIFEST_NAME
    if not manifest_path.is_file():
        raise CampaignError(f"{directory}: no {MANIFEST_NAME}")
    return LoadedCampaign(
        manifest=load_manifest(manifest_path),
        directory=directory.resolve(),
        manifest_path=manifest_path.resolve(),
    )


def json_schema() -> dict[str, Any]:
    """JSON Schema for ``campaign.yaml``, for editors and validators."""
    return CampaignManifest.model_json_schema()


def _render(exc: ValidationError) -> str:
    """Format validation errors as one readable line each."""
    lines = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error["loc"]) or "<root>"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
