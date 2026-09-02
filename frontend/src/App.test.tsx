/**
 * One test per route: mount the real `App` at that hash and assert the page
 * rendered.
 *
 * Nothing is stubbed above `fetch`, so the router, the query client and the
 * whole `api/client` layer run for real against bodies captured from a live
 * Gauntlet. A page that fails to render fails here.
 */

import { render, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import * as fixtures from "./test/fixtures";

const TEXT: Record<string, string> = {
  [`/api/runs/${fixtures.RUN_ID}/artifacts/test.log`]: "system_stats: starting\nsample 1 ok\n",
  [`/api/runs/${fixtures.RUN_ID}/artifacts/summary.md`]:
    "# system_stats\n\nAll samples within limits.\n",
  [`/api/runs/${fixtures.RUN_ID}/artifacts/verdict.json`]: JSON.stringify(fixtures.runVerdict),
  [`/api/runs/${fixtures.RUN_ID}/artifacts/manifest.json`]: JSON.stringify(fixtures.runManifest),
};

const JSON_BODIES: Record<string, unknown> = {
  "/api/health": fixtures.health,
  "/api/instruments": fixtures.instruments,
  "/api/runs": fixtures.runs,
  [`/api/runs/${fixtures.RUN_ID}`]: fixtures.run,
  [`/api/runs/${fixtures.RUN_ID}/artifacts`]: fixtures.runArtifacts,
  [`/api/runs/${fixtures.RUN_ID}/metrics`]: fixtures.runMetrics,
  [`/api/runs/${fixtures.RUN_ID}/notes`]: fixtures.runNotes,
  "/api/schemas": fixtures.schemas,
  "/api/settings": fixtures.settings,
  "/api/suites": fixtures.suites,
  "/api/suites/system_stats": fixtures.suite,
  "/api/suites/system_stats/profile-schema": fixtures.profileSchema,
  "/api/suites/system_stats/profiles/smoke.yaml": fixtures.profile,
  "/api/system/data": fixtures.systemData,
  "/api/system/info": fixtures.systemInfo,
  "/api/units": fixtures.units,
  [`/api/units/${fixtures.UNIT_SERIAL}`]: fixtures.unit,
  [`/api/units/${fixtures.UNIT_SERIAL}/history`]: fixtures.unitHistory,
  [`/api/units/${fixtures.UNIT_SERIAL}/notes`]: fixtures.unitNotes,
};

/** Answer one request from the captured bodies, or 404 the way the API would. */
function respond(input: RequestInfo | URL): Response {
  const path = new URL(String(input), "http://localhost").pathname;
  if (path in TEXT) {
    return new Response(TEXT[path], { headers: { "Content-Type": "text/plain" } });
  }
  const body = JSON_BODIES[path];
  if (body === undefined) {
    return new Response(JSON.stringify({ detail: `no fixture for ${path}` }), { status: 404 });
  }
  return new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" } });
}

function open(route: string) {
  window.location.hash = route;
  return render(<App />);
}

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => Promise.resolve(respond(input)))
  );
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.location.hash = "";
});

describe("every route renders", () => {
  it("shows the shell with a link to each page", async () => {
    open("#/");
    const nav = within(await screen.findByRole("navigation", { name: "Primary" }));
    for (const label of ["Dashboard", "History", "Tests", "Units", "Instruments", "System"]) {
      expect(nav.getByRole("link", { name: label })).toBeInTheDocument();
    }
  });

  it("dashboard lists the instruments and the recent runs", async () => {
    open("#/");
    expect(await screen.findByRole("heading", { name: "Dashboard" })).toBeInTheDocument();
    // A heading, not the nav tab of the same name.
    expect(await screen.findByRole("heading", { name: "Instruments" })).toBeInTheDocument();
    // The panels fed by the run history: recent runs, and the units behind them.
    expect(await screen.findByText("Recent runs")).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: "all units (2) →" })).toBeInTheDocument();
    // Host telemetry belongs to the system page.
    expect(screen.queryByText("Host stats")).not.toBeInTheDocument();
  });

  it("history lists the recorded runs and the server's total", async () => {
    open("#/history");
    expect(await screen.findByRole("heading", { name: "History" })).toBeInTheDocument();
    expect(await screen.findByText(/of 2 · page 1 of 1/)).toBeInTheDocument();
    expect(await screen.findByRole("link", { name: fixtures.UNIT_SERIAL })).toBeInTheDocument();
  });

  it("tests lists the catalog and opens a suite from the query string", async () => {
    open("#/tests?suite=system_stats");
    expect(await screen.findByRole("heading", { name: "Tests" })).toBeInTheDocument();
    const detail = await screen.findByRole("region", { name: "System — Linux Stats" });
    expect(within(detail).getByText("system_stats")).toBeInTheDocument();
    expect(within(detail).getByText("Smoke")).toBeInTheDocument();
  });

  it("run view shows the verdict and the run's own figures", async () => {
    open(`#/runs/${fixtures.RUN_ID}`);
    expect(await screen.findByRole("heading", { name: "system_stats" })).toBeInTheDocument();
    expect(await screen.findByText(fixtures.RUN_ID)).toBeInTheDocument();
    expect(await screen.findByText("Samples")).toBeInTheDocument();
    expect(await screen.findByRole("tab", { name: "metrics" })).toBeInTheDocument();
  });

  it("units lists every unit with its counters", async () => {
    open("#/units");
    expect(await screen.findByRole("heading", { name: "Units" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: fixtures.UNIT_SERIAL })).toBeInTheDocument();
  });

  it("one unit shows its counters, its history and its notes", async () => {
    open(`#/units/${fixtures.UNIT_SERIAL}`);
    expect(await screen.findByRole("heading", { name: fixtures.UNIT_SERIAL })).toBeInTheDocument();
    expect(await screen.findByText("Pass rate over time")).toBeInTheDocument();
    expect(await screen.findByText(fixtures.unitNotes.notes[0].body)).toBeInTheDocument();
  });

  it("instruments renders a panel per registered provider", async () => {
    open("#/instruments");
    expect(await screen.findByRole("heading", { name: "Instruments" })).toBeInTheDocument();
    for (const instrument of fixtures.instruments.instruments) {
      expect(
        await screen.findByRole("heading", { name: new RegExp(instrument.name) })
      ).toBeInTheDocument();
    }
  });

  it("system shows the host, the version it runs, and its telemetry", async () => {
    open("#/system");
    expect(await screen.findByRole("heading", { name: "System" })).toBeInTheDocument();
    expect(await screen.findByText(String(fixtures.systemInfo.hostname))).toBeInTheDocument();
    expect(await screen.findByText(String(fixtures.settings.port))).toBeInTheDocument();
    // The readings reach the rows, not just their labels.
    expect(await screen.findByText("Host stats")).toBeInTheDocument();
    expect(
      await screen.findByText(String(fixtures.systemData.cpu_per_core.length))
    ).toBeInTheDocument();
  });

  it("an unknown route renders the not-found page", async () => {
    open("#/nowhere");
    expect(await screen.findByText(/not found/i)).toBeInTheDocument();
  });
});
