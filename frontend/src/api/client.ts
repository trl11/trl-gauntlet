/**
 * The only module that calls `fetch`.
 *
 * Every endpoint gets one typed function. The base URL comes from
 * `VITE_API_BASE` so a packaged desktop build can point at a backend on
 * another origin; an empty value means same origin.
 */

import type {
  ArtifactList,
  CapabilityList,
  Health,
  InstrumentCommandResult,
  InstrumentList,
  JsonSchema,
  MetricsResponse,
  Note,
  NoteList,
  ProfileContent,
  ProfileDiff,
  RescanResult,
  RunControlResult,
  RunList,
  RunManifest,
  RunRow,
  SavedProfile,
  Settings,
  StartRunBody,
  SuiteList,
  SystemData,
  SystemInfo,
  UnitDetail,
  UnitHistory,
  UnitList,
  Verdict,
  VerifyReport,
  Version,
} from "./types";

/** Base URL every request is prefixed with. Empty means the current origin. */
export const API_BASE: string = (import.meta.env?.VITE_API_BASE ?? "").replace(/\/+$/, "");

/** Absolute URL for an API path such as `/api/runs`. */
export function apiUrl(path: string): string {
  return `${API_BASE}${path}`;
}

/** A non-2xx response, or a request that never reached the backend. */
export class ApiError extends Error {
  /** HTTP status, or 0 when the request failed before a response arrived. */
  readonly status: number;
  /** The `detail` field FastAPI returns, flattened to one line. */
  readonly detail: string;
  /** The URL that produced the failure. */
  readonly url: string;

  constructor(status: number, detail: string, url: string) {
    super(detail || `request failed with status ${status}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.url = url;
  }
}

function flattenDetail(payload: unknown, fallback: string): string {
  if (typeof payload === "string") return payload || fallback;
  if (Array.isArray(payload)) {
    const parts = payload.map((entry) => flattenDetail(entry, "")).filter(Boolean);
    return parts.join("; ") || fallback;
  }
  if (payload && typeof payload === "object") {
    const record = payload as Record<string, unknown>;
    if ("detail" in record) return flattenDetail(record.detail, fallback);
    if (typeof record.msg === "string") {
      const location = Array.isArray(record.loc) ? record.loc.join(".") : "";
      return location ? `${location}: ${record.msg}` : record.msg;
    }
  }
  return fallback;
}

async function readDetail(response: Response): Promise<string> {
  const text = await response.text().catch(() => "");
  const fallback = `${response.status} ${response.statusText || "error"}`.trim();
  if (!text) return fallback;
  try {
    return flattenDetail(JSON.parse(text), fallback);
  } catch {
    return text.slice(0, 500) || fallback;
  }
}

/**
 * Issue one request and decode its JSON body.
 *
 * Throws {@link ApiError} for any non-2xx response and for transport
 * failures. A 204 resolves to `undefined`.
 */
async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = apiUrl(path);
  const headers = new Headers(init?.headers);
  if (init?.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");

  let response: Response;
  try {
    response = await fetch(url, { ...init, headers });
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `cannot reach the Gauntlet API: ${message}`, url);
  }

  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response), url);
  }
  if (response.status === 204) return undefined as T;

  const body = await response.text();
  if (!body) return undefined as T;
  try {
    return JSON.parse(body) as T;
  } catch {
    throw new ApiError(response.status, "response was not valid JSON", url);
  }
}

/** Issue a request whose response is plain text rather than JSON. */
async function requestText(path: string, init?: RequestInit): Promise<string> {
  const url = apiUrl(path);
  let response: Response;
  try {
    response = await fetch(url, init);
  } catch (cause) {
    const message = cause instanceof Error ? cause.message : String(cause);
    throw new ApiError(0, `cannot reach the Gauntlet API: ${message}`, url);
  }
  if (!response.ok) {
    throw new ApiError(response.status, await readDetail(response), url);
  }
  return response.text();
}

type QueryValue = string | number | boolean | null | undefined;

/** Build a query string. An array repeats its key, which is how FastAPI reads a list. */
function query(params: Record<string, QueryValue | QueryValue[]>): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    for (const entry of Array.isArray(value) ? value : [value]) {
      if (entry === undefined || entry === null || entry === "") continue;
      search.append(key, String(entry));
    }
  }
  const encoded = search.toString();
  return encoded ? `?${encoded}` : "";
}

/** Percent-encode one path segment, so a run id or serial may contain any character. */
const encodeSegment = encodeURIComponent;

/* -------------------------------------------------------------------------
 * System
 * ---------------------------------------------------------------------- */

/** `GET /api/health` */
export const getHealth = (): Promise<Health> => request<Health>("/api/health");

/** `GET /api/version` */
export const getVersion = (): Promise<Version> => request<Version>("/api/version");

/** `GET /api/settings` */
export const getSettings = (): Promise<Settings> => request<Settings>("/api/settings");

/** `GET /api/system/info` */
export const getSystemInfo = (): Promise<SystemInfo> => request<SystemInfo>("/api/system/info");

/** `GET /api/system/data` */
export const getSystemData = (): Promise<SystemData> => request<SystemData>("/api/system/data");

/* -------------------------------------------------------------------------
 * Capabilities
 * ---------------------------------------------------------------------- */

/** `GET /api/capabilities` */
export const listCapabilities = (): Promise<CapabilityList> =>
  request<CapabilityList>("/api/capabilities");

/* -------------------------------------------------------------------------
 * Suites, profiles, schemas
 * ---------------------------------------------------------------------- */

/** `GET /api/suites` */
export const listSuites = (): Promise<SuiteList> => request<SuiteList>("/api/suites");

/** `POST /api/suites/rescan` */
export const rescanSuites = (): Promise<RescanResult> =>
  request<RescanResult>("/api/suites/rescan", { method: "POST" });

/** `GET /api/suites/{key}/profile-schema` */
export const getProfileSchema = (key: string): Promise<JsonSchema> =>
  request<JsonSchema>(`/api/suites/${encodeSegment(key)}/profile-schema`);

/** `GET /api/suites/{key}/profiles/{name}` */
export const getProfile = (key: string, name: string): Promise<ProfileContent> =>
  request<ProfileContent>(`/api/suites/${encodeSegment(key)}/profiles/${encodeSegment(name)}`);

/** `PUT /api/suites/{key}/profiles/{name}` */
export const saveProfile = (key: string, name: string, body: string): Promise<SavedProfile> =>
  request<SavedProfile>(`/api/suites/${encodeSegment(key)}/profiles/${encodeSegment(name)}`, {
    method: "PUT",
    body: JSON.stringify({ body }),
  });

/** `DELETE /api/suites/{key}/profiles/{name}` */
export const deleteProfile = (key: string, name: string): Promise<void> =>
  request<void>(`/api/suites/${encodeSegment(key)}/profiles/${encodeSegment(name)}`, {
    method: "DELETE",
  });

/** `POST /api/suites/{key}/profiles/{name}/diff` */
export const diffProfile = (key: string, name: string, content: string): Promise<ProfileDiff> =>
  request<ProfileDiff>(`/api/suites/${encodeSegment(key)}/profiles/${encodeSegment(name)}/diff`, {
    method: "POST",
    body: JSON.stringify({ content }),
  });

/** `POST /api/suites/{key}/profiles/{name}/duplicate` */
export const duplicateProfile = (
  key: string,
  name: string,
  newName: string
): Promise<SavedProfile> =>
  request<SavedProfile>(
    `/api/suites/${encodeSegment(key)}/profiles/${encodeSegment(name)}/duplicate`,
    {
      method: "POST",
      body: JSON.stringify({ name: newName }),
    }
  );

/** `POST /api/suites/{key}/verify` */
export const verifySuite = (key: string, execute = false): Promise<VerifyReport> =>
  request<VerifyReport>(`/api/suites/${encodeSegment(key)}/verify${query({ execute })}`, {
    method: "POST",
  });

/* -------------------------------------------------------------------------
 * Runs
 * ---------------------------------------------------------------------- */

/** Filters accepted by `GET /api/runs`. */
export interface ListRunsParams {
  /** Inclusive lower bound on `started_at`, as a date or a full timestamp. */
  after?: string | null;
  /** Inclusive upper bound on `started_at`. A bare date covers the whole day. */
  before?: string | null;
  direction?: "asc" | "desc";
  limit?: number;
  offset?: number;
  /** Column to order by. Anything the index does not know falls back to `started_at`. */
  sort?: string;
  /** Any run whose status is in this list. Empty accepts every status. */
  status?: string[];
  suite?: string | null;
  unit_serial?: string | null;
}

/** `GET /api/runs` */
export const listRuns = (params: ListRunsParams = {}): Promise<RunList> =>
  request<RunList>(`/api/runs${query({ ...params })}`);

/** `POST /api/runs` */
export const startRun = (body: StartRunBody): Promise<RunRow> =>
  request<RunRow>("/api/runs", { method: "POST", body: JSON.stringify(body) });

/** `GET /api/runs/{id}` */
export const getRun = (runId: string): Promise<RunRow> =>
  request<RunRow>(`/api/runs/${encodeSegment(runId)}`);

/** `POST /api/runs/{id}/stop` */
export const stopRun = (runId: string): Promise<RunControlResult> =>
  request<RunControlResult>(`/api/runs/${encodeSegment(runId)}/stop`, { method: "POST" });

/** `POST /api/runs/{id}/abort` */
export const abortRun = (runId: string): Promise<RunControlResult> =>
  request<RunControlResult>(`/api/runs/${encodeSegment(runId)}/abort`, { method: "POST" });

/** URL of the SSE stream for one run, resuming after sequence number `since`. */
export function runEventsUrl(runId: string, since: number): string {
  return apiUrl(`/api/runs/${encodeSegment(runId)}/events${query({ since })}`);
}

/** `GET /api/runs/{id}/artifacts` */
export const listArtifacts = (runId: string): Promise<ArtifactList> =>
  request<ArtifactList>(`/api/runs/${encodeSegment(runId)}/artifacts`);

/** URL of one artifact, for a download link or an `<img>` source. */
export function artifactUrl(runId: string, relative: string): string {
  const path = relative.split("/").map(encodeSegment).join("/");
  return apiUrl(`/api/runs/${encodeSegment(runId)}/artifacts/${path}`);
}

/** `GET /api/runs/{id}/artifacts/{path}`, as text. */
export const getArtifactText = (runId: string, relative: string): Promise<string> => {
  const path = relative.split("/").map(encodeSegment).join("/");
  return requestText(`/api/runs/${encodeSegment(runId)}/artifacts/${path}`);
};

/** `GET /api/runs/{id}/verdict` */
export const getRunVerdict = (runId: string): Promise<Verdict> =>
  request<Verdict>(`/api/runs/${encodeSegment(runId)}/verdict`);

/** `GET /api/runs/{id}/manifest` */
export const getRunManifest = (runId: string): Promise<RunManifest> =>
  request<RunManifest>(`/api/runs/${encodeSegment(runId)}/manifest`);

/** `GET /api/runs/{id}/metrics` */
export const getRunMetrics = (runId: string, limit?: number): Promise<MetricsResponse> =>
  request<MetricsResponse>(`/api/runs/${encodeSegment(runId)}/metrics${query({ limit })}`);

/** `GET /api/runs/{id}/notes` */
export const listRunNotes = (runId: string): Promise<NoteList> =>
  request<NoteList>(`/api/runs/${encodeSegment(runId)}/notes`);

/** `POST /api/runs/{id}/notes` */
export const addRunNote = (
  runId: string,
  body: string,
  author: string | null = null
): Promise<Note> =>
  request<Note>(`/api/runs/${encodeSegment(runId)}/notes`, {
    method: "POST",
    body: JSON.stringify({ body, author }),
  });

/** `DELETE /api/runs/{id}/notes/{note_id}` */
export const deleteRunNote = (runId: string, noteId: number): Promise<void> =>
  request<void>(`/api/runs/${encodeSegment(runId)}/notes/${noteId}`, { method: "DELETE" });

/* -------------------------------------------------------------------------
 * Units
 * ---------------------------------------------------------------------- */

/** `GET /api/units` */
export const listUnits = (): Promise<UnitList> => request<UnitList>("/api/units");

/** `GET /api/units/{serial}` */
export const getUnit = (serial: string): Promise<UnitDetail> =>
  request<UnitDetail>(`/api/units/${encodeSegment(serial)}`);

/** `PATCH /api/units/{serial}` */
export const renameUnit = (serial: string, newSerial: string): Promise<UnitDetail> =>
  request<UnitDetail>(`/api/units/${encodeSegment(serial)}`, {
    method: "PATCH",
    body: JSON.stringify({ serial: newSerial }),
  });

/** `DELETE /api/units/{serial}` */
export const deleteUnit = (serial: string): Promise<void> =>
  request<void>(`/api/units/${encodeSegment(serial)}`, { method: "DELETE" });

/** `GET /api/units/{serial}/history` */
export const getUnitHistory = (serial: string, limit?: number): Promise<UnitHistory> =>
  request<UnitHistory>(`/api/units/${encodeSegment(serial)}/history${query({ limit })}`);

/** `GET /api/units/{serial}/notes` */
export const listUnitNotes = (serial: string): Promise<NoteList> =>
  request<NoteList>(`/api/units/${encodeSegment(serial)}/notes`);

/** `POST /api/units/{serial}/notes` */
export const addUnitNote = (
  serial: string,
  body: string,
  author: string | null = null
): Promise<Note> =>
  request<Note>(`/api/units/${encodeSegment(serial)}/notes`, {
    method: "POST",
    body: JSON.stringify({ body, author }),
  });

/** `DELETE /api/units/{serial}/notes/{note_id}` */
export const deleteUnitNote = (serial: string, noteId: number): Promise<void> =>
  request<void>(`/api/units/${encodeSegment(serial)}/notes/${noteId}`, { method: "DELETE" });

/* -------------------------------------------------------------------------
 * Instruments
 * ---------------------------------------------------------------------- */

/** `GET /api/instruments` */
export const listInstruments = (): Promise<InstrumentList> =>
  request<InstrumentList>("/api/instruments");

/** `POST /api/instruments/scan` */
export const scanInstruments = (): Promise<InstrumentList> =>
  request<InstrumentList>("/api/instruments/scan", { method: "POST" });

/** `POST /api/instruments/{name}/command` */
export const sendInstrumentCommand = (
  name: string,
  command: string,
  args: Record<string, unknown> = {}
): Promise<InstrumentCommandResult> =>
  request<InstrumentCommandResult>(`/api/instruments/${encodeSegment(name)}/command`, {
    method: "POST",
    body: JSON.stringify({ command, args }),
  });
