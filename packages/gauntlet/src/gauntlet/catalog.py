"""Building the suite catalog from the configured roots and every campaign.

A campaign contributes its own suite directory to discovery, so the catalog is
never built from :attr:`Settings.suite_roots` alone. Everything that needs a
catalog goes through :func:`scan`, so the CLI and the server see the same set of
suites.
"""

from __future__ import annotations

from gauntlet.campaigns import CampaignCatalog, LoadedCampaign, discover_campaigns
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


def campaigns_by_suite(suites: SuiteCatalog, campaigns: CampaignCatalog) -> dict[str, LoadedCampaign]:
    """Suite key to the campaign whose suite directory holds it.

    Which campaign a suite belongs to is where it sits on disk, so this is a
    lookup rather than anything recorded. Callers build it once and read it per
    run, because a page of run history asks the same question of the same
    handful of suites.
    """
    if not campaigns.campaigns:
        return {}
    owners: dict[str, LoadedCampaign] = {}
    for key, suite in suites.suites.items():
        owner = campaigns.for_path(suite.directory)
        if owner is not None:
            owners[key] = owner
    return owners
