"""Suite catalog, profiles, schemas, and conformance endpoints."""

from __future__ import annotations

import json
import subprocess
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from gauntlet_suite.contract import CONTRACT_MODELS, json_schema

from gauntlet.conformance import verify_suite
from gauntlet.suites import list_profiles, resolve_profile
from gauntlet.supervisor.launcher import suite_environment

router = APIRouter()

_PROFILE_SCHEMA_TIMEOUT_S = 15.0


def _catalog(request: Request) -> Any:
    return request.app.state.catalog()


def _suite_or_404(request: Request, key: str) -> Any:
    suite = _catalog(request).get(key)
    if suite is None:
        raise HTTPException(status_code=404, detail=f"unknown suite {key!r}")
    return suite


@router.get("/suites")
async def get_suites(request: Request) -> dict[str, Any]:
    """Every discovered suite, with its profiles."""
    catalog = _catalog(request)
    settings = request.app.state.settings
    profiles = {key: list_profiles(suite, settings.profiles_dir) for key, suite in catalog.suites.items()}
    return catalog.to_dict(profiles)


@router.post("/suites/rescan")
async def rescan_suites(request: Request) -> dict[str, Any]:
    """Re-read the suite roots."""
    catalog = request.app.state.rescan()
    return {"count": len(catalog.suites), "errors": catalog.errors}


@router.get("/suites/{key}")
async def get_suite(request: Request, key: str) -> dict[str, Any]:
    """One suite, with its profiles."""
    suite = _suite_or_404(request, key)
    settings = request.app.state.settings
    payload = suite.to_dict()
    payload["profiles_available"] = [p.to_dict() for p in list_profiles(suite, settings.profiles_dir)]
    return payload


@router.get("/suites/{key}/profile-schema")
async def get_profile_schema(request: Request, key: str) -> dict[str, Any]:
    """JSON Schema for this suite's profile, for rendering an editor form.

    Produced by the suite's ``exec.profile_schema_command``.
    """
    suite = _suite_or_404(request, key)
    command = suite.manifest.exec.profile_schema_command
    if not command:
        raise HTTPException(
            status_code=404,
            detail=f"suite {key!r} does not declare exec.profile_schema_command",
        )
    try:
        completed = subprocess.run(
            command,
            cwd=str(suite.workdir),
            env=suite_environment(suite),
            capture_output=True,
            text=True,
            timeout=_PROFILE_SCHEMA_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise HTTPException(status_code=502, detail=f"profile schema command failed: {exc}") from exc
    if completed.returncode != 0:
        raise HTTPException(
            status_code=502,
            detail=f"profile schema command exited {completed.returncode}: {completed.stderr.strip()[:400]}",
        )
    try:
        return dict(json.loads(completed.stdout))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail=f"profile schema command did not print JSON: {exc}") from exc


@router.get("/suites/{key}/profiles/{name}")
async def get_profile(request: Request, key: str, name: str) -> dict[str, Any]:
    """Raw text of one profile, for the editor."""
    suite = _suite_or_404(request, key)
    settings = request.app.state.settings
    path = resolve_profile(suite, name, settings.profiles_dir)
    if path is None:
        raise HTTPException(status_code=404, detail=f"profile {name!r} not found for suite {key!r}")
    try:
        body = path.read_text()
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"name": path.name, "path": str(path), "body": body}


@router.put("/suites/{key}/profiles/{name}")
async def put_profile(request: Request, key: str, name: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Save an operator-authored profile.

    Writes to the user profile directory; a suite's own files are not modified.
    """
    suite = _suite_or_404(request, key)
    body = payload.get("body")
    if not isinstance(body, str):
        raise HTTPException(status_code=422, detail="`body` must be a string")
    if "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(status_code=422, detail="invalid profile name")
    filename = name if name.endswith((".yaml", ".yml")) else f"{name}.yaml"

    settings = request.app.state.settings
    directory = settings.profiles_dir / suite.key
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    try:
        path.write_text(body)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {"name": filename, "path": str(path), "user_authored": True}


@router.post("/suites/{key}/verify")
async def post_verify(request: Request, key: str, execute: bool = False) -> dict[str, Any]:
    """Run the conformance checks against a suite."""
    suite = _suite_or_404(request, key)
    return verify_suite(suite.directory, execute=execute).to_dict()


@router.get("/schemas")
async def get_schemas() -> dict[str, Any]:
    """Names of the contract schemas available."""
    return {"schemas": sorted(CONTRACT_MODELS)}


@router.get("/schemas/{name}")
async def get_schema(name: str) -> dict[str, Any]:
    """JSON Schema for one contract model, generated from the pydantic source."""
    try:
        return json_schema(name)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
