"""Campaign discovery and the ``campaign.yaml`` loader."""

from __future__ import annotations

from gauntlet.campaigns.discovery import CampaignCatalog, discover_campaigns
from gauntlet.campaigns.manifest import (
    CAMPAIGN_VERSION,
    MANIFEST_NAME,
    CampaignError,
    CampaignManifest,
    CampaignMember,
    LoadedCampaign,
    json_schema,
    load_campaign,
    load_manifest,
)

__all__ = [
    "CAMPAIGN_VERSION",
    "MANIFEST_NAME",
    "CampaignCatalog",
    "CampaignError",
    "CampaignManifest",
    "CampaignMember",
    "LoadedCampaign",
    "discover_campaigns",
    "json_schema",
    "load_campaign",
    "load_manifest",
]
