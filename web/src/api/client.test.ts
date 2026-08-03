import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  addUnitNote,
  apiUrl,
  artifactUrl,
  getRun,
  listRuns,
  renameUnit,
  runEventsUrl,
  sendInstrumentCommand,
  startRun,
  stopRun,
} from "./client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  fetchMock = vi.fn();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function lastCall(): [string, RequestInit] {
  const call = fetchMock.mock.calls.at(-1);
  if (!call) throw new Error("fetch was never called");
  return call as [string, RequestInit];
}

describe("url building", () => {
  it("prefixes paths with the configured base", () => {
    expect(apiUrl("/api/runs")).toBe("/api/runs");
  });

  it("encodes the run id and the since cursor in the event stream url", () => {
    expect(runEventsUrl("2026 01", 12)).toBe("/api/runs/2026%2001/events?since=12");
  });

  it("keeps artifact path separators while escaping each segment", () => {
    expect(artifactUrl("r1", "frames/a b.png")).toBe("/api/runs/r1/artifacts/frames/a%20b.png");
  });
});

describe("request", () => {
  it("passes query parameters and returns the decoded body", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ runs: [] }));
    await expect(listRuns({ suite: "demo", limit: 5 })).resolves.toEqual({ runs: [] });
    expect(lastCall()[0]).toBe("/api/runs?suite=demo&limit=5");
  });

  it("omits empty query parameters", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ runs: [] }));
    await listRuns({ suite: null, unit_serial: "" });
    expect(lastCall()[0]).toBe("/api/runs");
  });

  it("sends JSON bodies with a content type", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ run_id: "r1" }, 201));
    await startRun({ suite: "demo", profile: "mock.yaml" });
    const [url, init] = lastCall();
    expect(url).toBe("/api/runs");
    expect(init.method).toBe("POST");
    expect(init.body).toBe(JSON.stringify({ suite: "demo", profile: "mock.yaml" }));
    expect(new Headers(init.headers).get("Content-Type")).toBe("application/json");
  });

  it("resolves a 204 to undefined", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));
    await expect(stopRun("r1")).resolves.toBeUndefined();
  });
});

describe("error mapping", () => {
  it("carries the status and the detail string", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: "unknown run 'r9'" }, 404));
    const error = await getRun("r9").catch((caught: unknown) => caught);
    expect(error).toBeInstanceOf(ApiError);
    expect((error as ApiError).status).toBe(404);
    expect((error as ApiError).detail).toBe("unknown run 'r9'");
    expect((error as ApiError).message).toBe("unknown run 'r9'");
  });

  it("flattens a FastAPI validation detail list", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ detail: [{ loc: ["body", "serial"], msg: "field required" }] }, 422)
    );
    const error = (await renameUnit("A", "").catch((caught) => caught)) as ApiError;
    expect(error.status).toBe(422);
    expect(error.detail).toBe("body.serial: field required");
  });

  it("falls back to the raw text when the body is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("upstream exploded", { status: 502 }));
    const error = (await getRun("r1").catch((caught) => caught)) as ApiError;
    expect(error.status).toBe(502);
    expect(error.detail).toBe("upstream exploded");
  });

  it("reports a transport failure as status zero", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await getRun("r1").catch((caught) => caught)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.detail).toContain("cannot reach the Gauntlet API");
  });
});

describe("endpoint shapes", () => {
  it("posts a note with an explicit null author", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 1 }));
    await addUnitNote("SN-1", "looks fine");
    const [url, init] = lastCall();
    expect(url).toBe("/api/units/SN-1/notes");
    expect(init.body).toBe(JSON.stringify({ body: "looks fine", author: null }));
  });

  it("patches a unit with its new serial", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ serial: "SN-2" }));
    await renameUnit("SN-1", "SN-2");
    const [url, init] = lastCall();
    expect(url).toBe("/api/units/SN-1");
    expect(init.method).toBe("PATCH");
    expect(init.body).toBe(JSON.stringify({ serial: "SN-2" }));
  });

  it("wraps instrument command arguments", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ state: {}, result: {} }));
    await sendInstrumentCommand("psu", "set_voltage", { voltage_v: 12 });
    const [url, init] = lastCall();
    expect(url).toBe("/api/instruments/psu/command");
    expect(init.body).toBe(JSON.stringify({ command: "set_voltage", args: { voltage_v: 12 } }));
  });
});
