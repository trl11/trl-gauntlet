"""Campaign catalog, manifest editing, and per-campaign coverage.

A campaign groups suites and records how each is meant to be run; it does not
sequence them. Coverage is derived from the runs index by suite key, so nothing
is recorded on a run and the association survives rebuilding that index from
disk.

The manifest on disk is the source of truth. Editing it through this router
writes that file and rescans, so the same change can be made with an editor and
picked up with :func:`rescan_campaigns` instead.
"""

from __future__ import annotations

from typing import Any

import yaml
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from gauntlet.api.runs import to_row
from gauntlet.campaigns import CampaignError, json_schema, load_manifest
from gauntlet.storage.runs import RunFilters
from gauntlet.supervisor import RunConflict, RunRejected, RunRequest

router = APIRouter()


class CampaignManifestBody(BaseModel):
    """Request body carrying an edited ``campaign.yaml``.

    The field is named as `GET /campaigns/{key}/manifest` returns it, so
    reading a manifest, saving it and diffing it all speak of its `body`.
    """

    model_config = ConfigDict(extra="forbid")

    body: str


class MemberRunBody(BaseModel):
    """Overrides applied on top of what a member declares.

    Every field is optional: an empty body runs the member exactly as the
    campaign declares it.
    """

    model_config = ConfigDict(extra="forbid")

    profile: str | None = None
    target: str | None = None
    unit_serial: str | None = None
    overrides: dict[str, Any] = Field(default_factory=dict)


def _campaigns(request: Request) -> Any:
    return request.app.state.campaigns()


def _campaign_or_404(request: Request, key: str) -> Any:
    campaign = _campaigns(request).get(key)
    if campaign is None:
        raise HTTPException(status_code=404, detail=f"unknown campaign {key!r}")
    return campaign


def _member_suites(request: Request, campaign: Any) -> list[str]:
    """Suite keys belonging to this campaign, in test-plan order.

    Membership is the campaign's suite directory. Declared members come first,
    in manifest order, so the listing follows the test plan; a suite found in
    the directory but not declared follows, sorted.
    """
    catalog = request.app.state.catalog()
    owned = {key for key, suite in catalog.suites.items() if campaign.owns(suite.directory)}
    declared = [m.suite for m in campaign.manifest.members]
    return declared + sorted(owned - set(declared))


def _coverage(request: Request, suite_key: str) -> dict[str, Any]:
    """What the runs index knows about one member suite."""
    index = request.app.state.runs_index
    latest = index.list(RunFilters(suite=suite_key), limit=1)
    return {
        "run_count": index.count(RunFilters(suite=suite_key)),
        "passed": index.count(RunFilters(suite=suite_key, status=("passed",))),
        "failed": index.count(RunFilters(suite=suite_key, status=("failed",))),
        "last_run": latest[0].to_dict() if latest else None,
    }


def _member_payload(request: Request, campaign: Any, suite_key: str) -> dict[str, Any]:
    """One member: what the campaign declares, and what the suite catalog holds."""
    suite = request.app.state.catalog().get(suite_key)
    member = campaign.manifest.member(suite_key)
    payload: dict[str, Any] = {
        "suite": suite_key,
        # A declared member whose suite is not on disk. The campaign still
        # lists it, so a missing suite is visible rather than silently absent.
        "present": suite is not None,
        "title": suite.manifest.title if suite is not None else "",
        "declared": member is not None,
    }
    if member is not None:
        payload.update(member.model_dump(mode="json", exclude={"suite"}))
    payload.update(_coverage(request, suite_key))
    return payload


def _campaign_payload(request: Request, campaign: Any, *, members: bool) -> dict[str, Any]:
    payload = campaign.to_dict()
    keys = _member_suites(request, campaign)
    payload["member_count"] = len(keys)
    if members:
        payload["members"] = [_member_payload(request, campaign, key) for key in keys]
    else:
        payload.pop("members", None)
    return payload


@router.get("/campaigns/schema")
async def get_campaign_schema() -> dict[str, Any]:
    """JSON Schema for ``campaign.yaml``.

    Generated from the model rather than stored, so an editor pointed at this
    URL validates against the running Gauntlet.
    """
    return json_schema()


@router.get("/campaigns")
async def get_campaigns(request: Request) -> dict[str, Any]:
    """Every discovered campaign, without its members resolved."""
    catalog = _campaigns(request)
    return {
        "campaigns": [
            _campaign_payload(request, catalog.campaigns[key], members=False) for key in sorted(catalog.campaigns)
        ],
        "errors": list(catalog.errors),
    }


@router.post("/campaigns/rescan")
async def rescan_campaigns(request: Request) -> dict[str, Any]:
    """Re-read the campaign roots and the suites they contribute.

    This is the whole update path for a change made with an editor: save the
    file, or drop a suite into a campaign's suite directory, then rescan.
    Nothing is rebuilt and the process keeps running.
    """
    request.app.state.rescan()
    return await get_campaigns(request)


@router.get("/campaigns/{key}")
async def get_campaign(request: Request, key: str) -> dict[str, Any]:
    """One campaign, with every member suite and its coverage."""
    return _campaign_payload(request, _campaign_or_404(request, key), members=True)


@router.get("/campaigns/{key}/manifest")
async def get_campaign_manifest(request: Request, key: str) -> dict[str, Any]:
    """The campaign's ``campaign.yaml`` as text, for editing."""
    campaign = _campaign_or_404(request, key)
    try:
        body = campaign.manifest_path.read_text()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"key": key, "path": str(campaign.manifest_path), "body": body}


@router.put("/campaigns/{key}/manifest")
async def put_campaign_manifest(request: Request, key: str, body: CampaignManifestBody) -> dict[str, Any]:
    """Validate and save an edited ``campaign.yaml``, then rescan.

    The file is written only once it parses and validates, so a rejected edit
    leaves the campaign as it was. Renaming the key is refused: the caller
    addressed this campaign by the old one, and discovery is by directory, so
    the rename would take effect somewhere the caller is not looking.
    """
    campaign = _campaign_or_404(request, key)
    try:
        parsed = yaml.safe_load(body.body)
    except yaml.YAMLError as exc:
        raise HTTPException(status_code=422, detail=f"invalid YAML: {exc}") from exc
    if not isinstance(parsed, dict):
        raise HTTPException(status_code=422, detail="expected a mapping at the top level")
    if parsed.get("key") != key:
        raise HTTPException(status_code=422, detail=f"key must stay {key!r}")

    original = campaign.manifest_path.read_text()
    try:
        campaign.manifest_path.write_text(body.body)
        load_manifest(campaign.manifest_path)
    except CampaignError as exc:
        campaign.manifest_path.write_text(original)
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    request.app.state.rescan()
    return _campaign_payload(request, _campaign_or_404(request, key), members=True)


@router.post("/campaigns/{key}/members/{suite}/run", status_code=201)
async def run_member(request: Request, key: str, suite: str, body: MemberRunBody) -> dict[str, Any]:
    """Start one member's suite using what the campaign declares for it.

    A convenience over `POST /runs`, not a scheduler: it starts a single run,
    with the member's profile, target and overrides as the defaults. Re-running
    a member after editing the campaign is therefore this call again.
    """
    campaign = _campaign_or_404(request, key)
    if suite not in _member_suites(request, campaign):
        raise HTTPException(status_code=404, detail=f"suite {suite!r} is not a member of campaign {key!r}")
    member = campaign.manifest.member(suite)

    declared_target = member.target if member else ""
    declared_serial = member.unit_serial if member else ""
    overrides = dict(member.overrides) if member else {}
    overrides.update(body.overrides)

    try:
        handle = await request.app.state.supervisor.start(
            RunRequest(
                suite=suite,
                profile=body.profile or (member.profile if member else "") or None,
                target=body.target or declared_target or request.app.state.settings.default_target or None,
                unit_serial=body.unit_serial or declared_serial or None,
                overrides=overrides,
            )
        )
    except RunConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except RunRejected as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    request.app.state.runs_index.upsert(to_row(handle))
    return handle.to_dict()
