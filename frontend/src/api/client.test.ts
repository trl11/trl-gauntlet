import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  abortRun,
  addRunNote,
  addUnitNote,
  apiUrl,
  artifactUrl,
  deleteProfile,
  deleteRun,
  deleteRunNote,
  deleteUnit,
  deleteUnitNote,
  diffProfile,
  duplicateProfile,
  getArtifactText,
  getHealth,
  getProfile,
  getProfileSchema,
  getRun,
  getRunManifest,
  getRunMetrics,
  getRunVerdict,
  getSettings,
  getSystemData,
  getSystemInfo,
  getUnit,
  getUnitHistory,
  getVersion,
  listArtifacts,
  listCapabilities,
  listInstruments,
  listRunNotes,
  listRuns,
  listSuites,
  listUnitNotes,
  listUnits,
  renameUnit,
  rescanSuites,
  runEventsUrl,
  saveProfile,
  scanInstruments,
  sendInstrumentCommand,
  startRun,
  stopRun,
  verifySuite,
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

  // The base is read once, when the module is first evaluated, so reaching it
  // from the host means re-importing rather than reassigning.
  it("takes the base the Electron host supplies, trailing slash and all", async () => {
    vi.stubGlobal("window", { gauntlet: { apiBase: "http://127.0.0.1:41287/" } });
    vi.resetModules();
    const client = await import("./client");
    expect(client.API_BASE).toBe("http://127.0.0.1:41287");
    expect(client.apiUrl("/api/runs")).toBe("http://127.0.0.1:41287/api/runs");
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

describe("every endpoint addresses its route", () => {
  const endpoints: [string, () => Promise<unknown>, string, string][] = [
    ["getHealth", getHealth, "GET", "/api/health"],
    ["getVersion", getVersion, "GET", "/api/version"],
    ["getSettings", getSettings, "GET", "/api/settings"],
    ["getSystemInfo", getSystemInfo, "GET", "/api/system/info"],
    ["getSystemData", getSystemData, "GET", "/api/system/data"],
    ["listCapabilities", listCapabilities, "GET", "/api/capabilities"],
    ["listSuites", listSuites, "GET", "/api/suites"],
    ["rescanSuites", rescanSuites, "POST", "/api/suites/rescan"],
    ["getProfileSchema", () => getProfileSchema("demo"), "GET", "/api/suites/demo/profile-schema"],
    [
      "getProfile",
      () => getProfile("demo", "quick.yaml"),
      "GET",
      "/api/suites/demo/profiles/quick.yaml",
    ],
    [
      "saveProfile",
      () => saveProfile("demo", "mine.yaml", "iterations: 1\n"),
      "PUT",
      "/api/suites/demo/profiles/mine.yaml",
    ],
    [
      "deleteProfile",
      () => deleteProfile("demo", "mine.yaml"),
      "DELETE",
      "/api/suites/demo/profiles/mine.yaml",
    ],
    [
      "diffProfile",
      () => diffProfile("demo", "quick.yaml", "edited"),
      "POST",
      "/api/suites/demo/profiles/quick.yaml/diff",
    ],
    [
      "duplicateProfile",
      () => duplicateProfile("demo", "quick.yaml", "copy"),
      "POST",
      "/api/suites/demo/profiles/quick.yaml/duplicate",
    ],
    ["verifySuite", () => verifySuite("demo"), "POST", "/api/suites/demo/verify?execute=false"],
    [
      "verifySuite executing",
      () => verifySuite("demo", true),
      "POST",
      "/api/suites/demo/verify?execute=true",
    ],
    ["listRuns", () => listRuns(), "GET", "/api/runs"],
    ["getRun", () => getRun("r1"), "GET", "/api/runs/r1"],
    ["stopRun", () => stopRun("r1"), "POST", "/api/runs/r1/stop"],
    ["abortRun", () => abortRun("r1"), "POST", "/api/runs/r1/abort"],
    ["deleteRun", () => deleteRun("r1"), "DELETE", "/api/runs/r1"],
    ["listArtifacts", () => listArtifacts("r1"), "GET", "/api/runs/r1/artifacts"],
    ["getRunVerdict", () => getRunVerdict("r1"), "GET", "/api/runs/r1/verdict"],
    ["getRunManifest", () => getRunManifest("r1"), "GET", "/api/runs/r1/manifest"],
    ["getRunMetrics", () => getRunMetrics("r1"), "GET", "/api/runs/r1/metrics"],
    [
      "getRunMetrics limited",
      () => getRunMetrics("r1", 50),
      "GET",
      "/api/runs/r1/metrics?limit=50",
    ],
    ["listRunNotes", () => listRunNotes("r1"), "GET", "/api/runs/r1/notes"],
    ["addRunNote", () => addRunNote("r1", "note"), "POST", "/api/runs/r1/notes"],
    ["deleteRunNote", () => deleteRunNote("r1", 7), "DELETE", "/api/runs/r1/notes/7"],
    ["listUnits", listUnits, "GET", "/api/units"],
    ["getUnit", () => getUnit("SN-1"), "GET", "/api/units/SN-1"],
    ["deleteUnit", () => deleteUnit("SN-1"), "DELETE", "/api/units/SN-1?runs=true"],
    ["getUnitHistory", () => getUnitHistory("SN-1"), "GET", "/api/units/SN-1/history"],
    [
      "getUnitHistory limited",
      () => getUnitHistory("SN-1", 20),
      "GET",
      "/api/units/SN-1/history?limit=20",
    ],
    ["listUnitNotes", () => listUnitNotes("SN-1"), "GET", "/api/units/SN-1/notes"],
    ["deleteUnitNote", () => deleteUnitNote("SN-1", 3), "DELETE", "/api/units/SN-1/notes/3"],
    ["listInstruments", listInstruments, "GET", "/api/instruments"],
    ["scanInstruments", scanInstruments, "POST", "/api/instruments/scan"],
  ];

  it.each(endpoints)("%s issues %s %s", async (_name, call, method, url) => {
    fetchMock.mockResolvedValue(jsonResponse({}));
    await call();
    const [requested, init] = lastCall();
    expect(requested).toBe(url);
    expect(init?.method ?? "GET").toBe(method);
  });

  it("escapes a segment that contains a slash", async () => {
    fetchMock.mockResolvedValue(jsonResponse({}));
    await getUnit("SN/1");
    expect(lastCall()[0]).toBe("/api/units/SN%2F1");
  });

  it("repeats a key for each entry of an array filter", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ runs: [] }));
    await listRuns({ status: ["passed", "failed"] });
    expect(lastCall()[0]).toBe("/api/runs?status=passed&status=failed");
  });
});

describe("text responses", () => {
  it("returns the body verbatim", async () => {
    fetchMock.mockResolvedValue(new Response("line one\nline two"));
    await expect(getArtifactText("r1", "test.log")).resolves.toBe("line one\nline two");
    expect(lastCall()[0]).toBe("/api/runs/r1/artifacts/test.log");
  });

  it("maps a non-2xx to an ApiError", async () => {
    fetchMock.mockResolvedValue(new Response("not found", { status: 404 }));
    const error = (await getArtifactText("r1", "gone.log").catch((caught) => caught)) as ApiError;
    expect(error.status).toBe(404);
    expect(error.detail).toBe("not found");
  });

  it("reports a transport failure as status zero", async () => {
    fetchMock.mockRejectedValue(new TypeError("Failed to fetch"));
    const error = (await getArtifactText("r1", "test.log").catch((caught) => caught)) as ApiError;
    expect(error.status).toBe(0);
    expect(error.detail).toContain("cannot reach the Gauntlet API");
  });
});

describe("body decoding", () => {
  it("resolves an empty 200 body to undefined", async () => {
    fetchMock.mockResolvedValue(new Response("", { status: 200 }));
    await expect(getRun("r1")).resolves.toBeUndefined();
  });

  it("rejects a 200 whose body is not JSON", async () => {
    fetchMock.mockResolvedValue(new Response("<html>", { status: 200 }));
    const error = (await getRun("r1").catch((caught) => caught)) as ApiError;
    expect(error.status).toBe(200);
    expect(error.detail).toBe("response was not valid JSON");
  });

  it("falls back to the status line when the error body is empty", async () => {
    fetchMock.mockResolvedValue(new Response("", { status: 503, statusText: "Unavailable" }));
    const error = (await getRun("r1").catch((caught) => caught)) as ApiError;
    expect(error.detail).toBe("503 Unavailable");
  });

  it("unwraps a nested detail object", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: { msg: "too hot" } }, 400));
    const error = (await getRun("r1").catch((caught) => caught)) as ApiError;
    expect(error.detail).toBe("too hot");
  });

  it("falls back when the detail carries nothing readable", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ detail: [] }, 400));
    const error = (await getRun("r1").catch((caught) => caught)) as ApiError;
    expect(error.detail).toBe("400 error");
    expect(error.message).toBe("400 error");
  });

  it("uses a generic message when there is no detail at all", () => {
    expect(new ApiError(500, "", "/api/runs").message).toBe("request failed with status 500");
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
