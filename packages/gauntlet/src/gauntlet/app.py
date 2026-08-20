"""FastAPI application factory.

Wires the suite catalog, supervisor, capability registry, and run index onto
``app.state``, mounts the API under ``/api``, and serves the frontend bundle at
``/`` when one has been built.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from gauntlet.api import artifacts, campaigns, capabilities, instruments, runs, suites, system, units
from gauntlet.campaigns import CampaignCatalog
from gauntlet.capabilities import CapabilityRegistry
from gauntlet.catalog import scan
from gauntlet.config import Settings, load_settings
from gauntlet.instruments import detect_instruments
from gauntlet.storage import NotesIndex, RunRow, RunsIndex, UnitsIndex
from gauntlet.suites import SuiteCatalog
from gauntlet.supervisor import RunHandle, RunSupervisor

log = logging.getLogger("gauntlet")


def _web_dist() -> Path:
    return Path(__file__).parent / "web_dist"


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    settings = settings or load_settings()
    settings.ensure_dirs()

    @asynccontextmanager
    async def lifespan(instance: FastAPI) -> AsyncIterator[None]:
        instance.state.supervisor.attach_loop(asyncio.get_running_loop())
        yield

    app = FastAPI(
        title="Gauntlet",
        version=_app_version(),
        description="Runs test suites that conform to the Gauntlet contract.",
        lifespan=lifespan,
    )
    # Loopback and wildcard binds accept any origin; a specific bind is
    # restricted to its own origin.
    origins = (
        ["*"] if settings.host in {"0.0.0.0", "127.0.0.1", "localhost"} else [f"http://{settings.host}:{settings.port}"]
    )
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["*"], allow_headers=["*"])

    catalog, campaign_catalog = scan(settings)
    _log_catalog(catalog, campaign_catalog, settings)

    def current_catalog() -> SuiteCatalog:
        return app.state.suite_catalog

    def current_campaigns() -> CampaignCatalog:
        return app.state.campaign_catalog

    def rescan() -> SuiteCatalog:
        app.state.suite_catalog, app.state.campaign_catalog = scan(settings)
        return app.state.suite_catalog

    registry = CapabilityRegistry(api_base=settings.api_base)

    def detect() -> None:
        detect_instruments(registry, settings)

    # An instrument is registered only while its hardware answers. A scan runs
    # this again, so one attached after startup is picked up without a restart
    # and one unplugged stops being offered.
    detect()

    # Notes and units share the runs database: a unit is an aggregate over the
    # runs table, and a note points at a row in one of the two.
    runs_index = RunsIndex(settings.runs_index_path)
    notes_index = NotesIndex(settings.runs_index_path)
    units_index = UnitsIndex(settings.runs_index_path, notes_index)
    stale = runs_index.reconcile_stale()
    if stale:
        log.warning("marked %d interrupted run(s) from a previous session", stale)
    imported = runs_index.import_tree(settings.runs_dir)
    if imported:
        log.info("imported %d run(s) found on disk", imported)

    async def on_run_completed(handle: RunHandle) -> None:
        runs_index.upsert(
            RunRow(
                run_id=handle.run_id,
                suite=handle.suite,
                status=handle.status,
                started_at=handle.started_at,
                run_dir=handle.run_dir,
                ended_at=handle.ended_at,
                duration_s=handle.duration_s,
                verdict=handle.verdict,
                fail_reason=handle.fail_reason,
                profile=handle.profile,
                target=handle.target,
                unit_serial=handle.unit_serial,
            )
        )

    app.state.settings = settings
    app.state.suite_catalog = catalog
    app.state.campaign_catalog = campaign_catalog
    app.state.catalog = current_catalog
    app.state.campaigns = current_campaigns
    app.state.rescan = rescan
    app.state.capabilities = registry
    app.state.detect_instruments = detect
    app.state.notes_index = notes_index
    app.state.runs_index = runs_index
    app.state.units_index = units_index
    # The previous /proc/stat reading, which CPU percentages are measured
    # against. Empty until the first request for system data takes one.
    app.state.cpu_sample = {}
    app.state.supervisor = RunSupervisor(
        runs_dir=settings.runs_dir,
        user_profiles_dir=settings.profiles_dir,
        catalog_provider=current_catalog,
        capabilities=registry,
        api_base=settings.api_base,
        on_run_completed=on_run_completed,
    )

    # Tags group the API documentation by what a caller is working with, which
    # is coarser than the module split: a run's artifacts are read through the
    # run that wrote them, and the capability endpoints a suite drives are part
    # of the installation rather than a resource of their own.
    app.include_router(system.router, prefix="/api", tags=["system"])
    app.include_router(suites.router, prefix="/api", tags=["suites"])
    app.include_router(campaigns.router, prefix="/api", tags=["campaigns"])
    app.include_router(runs.router, prefix="/api", tags=["runs"])
    app.include_router(artifacts.router, prefix="/api", tags=["runs"])
    app.include_router(units.router, prefix="/api", tags=["units"])
    app.include_router(instruments.router, prefix="/api", tags=["instruments"])
    app.include_router(capabilities.router, prefix="/api", tags=["system"])

    _mount_frontend(app)
    return app


def _mount_frontend(app: FastAPI) -> None:
    """Serve the built SPA, or a link to the API docs when it is absent."""
    web_dir = _web_dist()
    index = web_dir / "index.html"
    if not index.is_file():

        @app.get("/", include_in_schema=False)
        async def _placeholder() -> HTMLResponse:
            return HTMLResponse(
                "<!doctype html><meta charset='utf-8'><title>Gauntlet</title>"
                "<body style='font-family:system-ui;padding:2rem;line-height:1.6'>"
                "<h1>Gauntlet</h1>"
                "<p>No frontend bundle is built. Run <code>make frontend</code>, or use the API directly.</p>"
                "<p><a href='/docs'>API documentation</a></p></body>"
            )

        return

    assets = web_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/", include_in_schema=False)
    async def _root() -> FileResponse:
        return FileResponse(index)

    @app.get("/{path:path}", include_in_schema=False, response_model=None)
    async def _spa(path: str) -> FileResponse:
        # Unknown /api routes return JSON 404 rather than the SPA shell.
        if path == "api" or path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        candidate = (web_dir / path).resolve()
        try:
            candidate.relative_to(web_dir.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid path") from exc
        return FileResponse(candidate if candidate.is_file() else index)


def _log_catalog(catalog: SuiteCatalog, campaigns: CampaignCatalog, settings: Settings) -> None:
    roots = ", ".join(str(p) for p in settings.suite_roots)
    log.info("discovered %d suite(s) in %s", len(catalog.suites), roots)
    for error in catalog.errors:
        log.warning("suite discovery: %s", error)
    campaign_roots = ", ".join(str(p) for p in settings.campaign_roots)
    log.info("discovered %d campaign(s) in %s", len(campaigns.campaigns), campaign_roots)
    for error in campaigns.errors:
        log.warning("campaign discovery: %s", error)


def _app_version() -> str:
    from gauntlet import __version__

    return __version__


__all__ = ["create_app"]
