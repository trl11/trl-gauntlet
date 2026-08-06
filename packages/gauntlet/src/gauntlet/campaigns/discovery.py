"""Finds campaigns on disk and maps suites back to the campaign that owns them.

Read-only: no subprocesses, no side effects. Mirrors suite discovery, so a
campaign directory added while Gauntlet is running appears after a rescan
rather than requiring a restart.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from gauntlet.campaigns.manifest import MANIFEST_NAME, CampaignError, LoadedCampaign, load_campaign

log = logging.getLogger("gauntlet.campaigns")

_MAX_DEPTH = 3


@dataclass
class CampaignCatalog:
    """Every campaign Gauntlet found, plus what went wrong for those it did not."""

    campaigns: dict[str, LoadedCampaign] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    def get(self, key: str) -> LoadedCampaign | None:
        return self.campaigns.get(key)

    def suite_roots(self) -> list[Path]:
        """The suite directory of every campaign, for the discovery roots.

        This is what makes a campaign's suites runnable: they are discovered as
        ordinary suites, so adding one to the directory needs a rescan and
        nothing more.
        """
        return [self.campaigns[key].suites_dir for key in sorted(self.campaigns)]

    def for_path(self, suite_directory: Path) -> LoadedCampaign | None:
        """The campaign whose suite directory contains this suite, if any.

        Membership is where a suite sits on disk, so a campaign picks up a suite
        dropped into its directory without naming it. It is derived rather than
        recorded: nothing is stored on a run, and the association survives
        rebuilding the run index from disk.
        """
        for key in sorted(self.campaigns):
            campaign = self.campaigns[key]
            if campaign.owns(suite_directory):
                return campaign
        return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "campaigns": [self.campaigns[key].to_dict() for key in sorted(self.campaigns)],
            "errors": list(self.errors),
        }


def discover_campaigns(roots: list[Path]) -> CampaignCatalog:
    """Walk the roots and load every ``campaign.yaml`` found.

    Malformed manifests are collected into :attr:`CampaignCatalog.errors` rather
    than raised. On a key collision the earlier root wins.
    """
    catalog = CampaignCatalog()
    for root in roots:
        if not root.is_dir():
            continue
        for manifest_path in _find_manifests(root):
            try:
                campaign = load_campaign(manifest_path.parent)
            except CampaignError as exc:
                catalog.errors.append(str(exc))
                log.warning("skipping campaign at %s: %s", manifest_path.parent, exc)
                continue
            existing = catalog.campaigns.get(campaign.key)
            if existing is not None:
                catalog.errors.append(
                    f"duplicate campaign key {campaign.key!r}: "
                    f"keeping {existing.directory}, ignoring {campaign.directory}"
                )
                continue
            catalog.campaigns[campaign.key] = campaign
    return catalog


def _find_manifests(root: Path) -> list[Path]:
    """Collect ``campaign.yaml`` files without descending into a campaign."""
    found: list[Path] = []

    def _walk(directory: Path, depth: int) -> None:
        if depth > _MAX_DEPTH:
            return
        manifest = directory / MANIFEST_NAME
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
