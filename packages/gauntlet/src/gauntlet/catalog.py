"""Building the suite catalog from the configured roots and every campaign.

A campaign contributes its own suite directory to discovery, so the catalog is
never built from :attr:`Settings.suite_roots` alone. Everything that needs a
catalog goes through :func:`scan`, so the CLI and the server see the same set of
suites.
"""

from __future__ import annotations

from gauntlet.campaigns import CampaignCatalog, discover_campaigns
from gauntlet.config import Settings
from gauntlet.suites import SuiteCatalog, discover_suites


def scan(settings: Settings) -> tuple[SuiteCatalog, CampaignCatalog]:
    """Read both catalogs, campaigns first.

    The configured suite roots are searched before any campaign's, and discovery
    keeps the earlier root on a key collision, so a suite shipped with Gauntlet
    wins over a campaign shadowing its key.
    """
    campaigns = discover_campaigns(settings.campaign_roots)
    return discover_suites([*settings.suite_roots, *campaigns.suite_roots()]), campaigns
