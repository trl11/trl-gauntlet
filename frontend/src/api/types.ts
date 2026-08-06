/**
 * Shapes returned by the Gauntlet REST API.
 *
 * Every interface mirrors what the Python side actually serialises:
 * `gauntlet_sdk.contract` for the four contract files, and the routers in
 * `gauntlet.api` for everything else. Models declared with `extra="allow"`
 * carry an index signature so unknown keys survive a round trip.
 */

/* -------------------------------------------------------------------------
 * Contract enumerations
 * ---------------------------------------------------------------------- */

/** Artifact a suite may declare in `produces`. */
export type ArtifactKind =
  "events" | "frames" | "junit" | "manifest" | "metrics" | "summary" | "verdict";

/** Type of a per-run override control. */
export type OverrideType = "boolean" | "integer" | "number" | "string";

/** How a headline result figure is rendered. */
export type ResultFormat = "bytes" | "decimal" | "duration" | "int" | "percent" | "text";

/** Signal a suite accepts for a graceful stop. */
export type StopSignal = "SIGUSR1" | "SIGINT" | "SIGTERM" | "NONE";

/** Outcome of one test row inside a verdict. */
export type TestOutcome = "error" | "fail" | "pass" | "skip";

/** Kind of one `metrics.jsonl` record. */
export type MetricsKind = "anomaly" | "iteration" | "live";

/** Lifecycle state of a run. */
export type RunStatus =
  | "aborted"
  | "aborting"
  | "error"
  | "failed"
  | "interrupted"
  | "passed"
  | "running"
  | "starting"
  | "stopping";

/** Short verdict code recorded alongside the status. */
export type RunVerdictCode = "ABORTED" | "ERROR" | "FAIL" | "PASS";

/** Severity inferred for one captured stdout line. */
export type LogLevel = "error" | "info" | "warning";

/* -------------------------------------------------------------------------
 * JSON Schema
 * ---------------------------------------------------------------------- */

/** A JSON Schema document, as generated from the pydantic contract models. */
export interface JsonSchema {
  $defs?: Record<string, JsonSchema>;
  $ref?: string;
  additionalProperties?: boolean | JsonSchema;
  anyOf?: JsonSchema[];
  const?: unknown;
  default?: unknown;
  description?: string;
  enum?: unknown[];
  exclusiveMaximum?: number;
  exclusiveMinimum?: number;
  format?: string;
  items?: JsonSchema;
  maximum?: number;
  maxLength?: number;
  minimum?: number;
  minLength?: number;
  pattern?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  title?: string;
  type?: string | string[];
  [key: string]: unknown;
}

/* -------------------------------------------------------------------------
 * Suites and profiles
 * ---------------------------------------------------------------------- */

/** How Gauntlet launches the suite process. */
export interface SuiteExec {
  args: Record<string, string>;
  command: string[];
  env: Record<string, string>;
  graceful_stop_signal: StopSignal;
  profile_schema_command: string[];
  workdir: string;
}

/** One per-run knob an operator may set. */
export interface SuiteOverride {
  choices: string[];
  default: unknown;
  flag: string;
  help: string;
  label: string;
  /** Highest accepted value, for a `number` or an `integer`. */
  maximum: number | null;
  /** Lowest accepted value, for a `number` or an `integer`. */
  minimum: number | null;
  name: string;
  type: OverrideType;
  unit: string;
}

/** Optional run inputs a suite accepts. */
export interface SuiteSupports {
  target: boolean;
  unit_serial: boolean;
}

/** One profile file offered for a suite. */
export interface SuiteProfile {
  description: string;
  name: string;
  path: string;
  user_authored: boolean;
}

/** A discovered suite: its `suite.yaml` plus where it was found. */
export interface Suite {
  apiVersion: 1;
  category: string;
  conformance_profile: string;
  default_metrics: string[];
  description: string;
  directory: string;
  exec: SuiteExec;
  key: string;
  overrides: SuiteOverride[];
  produces: ArtifactKind[];
  profiles: string;
  profiles_available?: SuiteProfile[];
  requires: string[];
  supports: SuiteSupports;
  title: string;
}

/** `GET /api/suites`, and `POST /api/suites/rescan`. */
export interface SuiteList {
  errors: string[];
  suites: Suite[];
}

/** `GET /api/suites/{key}/profiles/{name}`. */
export interface ProfileContent {
  body: string;
  name: string;
  path: string;
}

/** What a profile write returns. */
export interface SavedProfile {
  name: string;
  path: string;
  user_authored: boolean;
}

/** `POST /api/suites/{key}/profiles/{name}/diff`. */
export interface ProfileDiff {
  diff: string;
}

/* -------------------------------------------------------------------------
 * Campaigns
 * ---------------------------------------------------------------------- */

/**
 * One suite in a campaign, as the manifest declares it.
 *
 * What that suite has done is not here: a run names its campaign instead, so
 * history is read from the runs index rather than from the campaign.
 *
 * Every field is present whether or not the manifest declares this member;
 * `declared` says which. `present` is false for a member the manifest names but
 * whose suite is not on disk.
 */
export interface CampaignMember {
  component: string;
  declared: boolean;
  fixture: string;
  host: string;
  notes: string;
  overrides: Record<string, unknown>;
  present: boolean;
  profile: string;
  suite: string;
  target: string;
  test_vehicle: string;
  title: string;
  unit_serial: string;
}

/**
 * `GET /api/campaigns/{key}`, and one entry of `GET /api/campaigns`.
 *
 * The listing omits `members`; only the detail endpoint resolves them.
 */
export interface Campaign {
  apiVersion: 1;
  description: string;
  directory: string;
  key: string;
  member_count: number;
  members?: CampaignMember[];
  suites: string;
  suites_dir: string;
  title: string;
}

/** `GET /api/campaigns`, and `POST /api/campaigns/rescan`. */
export interface CampaignList {
  campaigns: Campaign[];
  errors: string[];
}

/* -------------------------------------------------------------------------
 * Conformance
 * ---------------------------------------------------------------------- */

/** One conformance finding. */
export interface VerifyCheck {
  detail: string;
  fatal: boolean;
  name: string;
  passed: boolean;
}

/** `POST /api/suites/{key}/verify`. */
export interface VerifyReport {
  checks: VerifyCheck[];
  directory: string;
  executed: boolean;
  passed: boolean;
  run_dir: string;
  suite: string;
}

/* -------------------------------------------------------------------------
 * Runs
 * ---------------------------------------------------------------------- */

/**
 * The campaign that groups a run's suite.
 *
 * Derived from the suite key when the run is read, never recorded on it: it
 * says which campaign holds that suite now, not that the campaign started the
 * run.
 */
export interface RunCampaign {
  key: string;
  title: string;
}

/** One run, live or from the index. A live run also carries the argv it was spawned with. */
export interface RunRow {
  argv?: string[];
  campaign?: RunCampaign | null;
  duration_s: number | null;
  ended_at: string | null;
  fail_reason: string | null;
  profile: string | null;
  run_dir: string;
  run_id: string;
  started_at: string;
  status: RunStatus;
  suite: string;
  target: string | null;
  unit_serial: string | null;
  verdict: RunVerdictCode | null;
}

/** `GET /api/runs`. `total` counts every match, not just this page. */
export interface RunList {
  runs: RunRow[];
  total: number;
}

/** Body of `POST /api/runs`. */
export interface StartRunBody {
  overrides?: Record<string, unknown>;
  profile?: string | null;
  profile_body?: string | null;
  suite: string;
  target?: string | null;
  unit_serial?: string | null;
}

/** What `stop` and `abort` acknowledge with. */
export interface RunControlResult {
  run_id: string;
  status: string;
}

/* -------------------------------------------------------------------------
 * Artifacts
 * ---------------------------------------------------------------------- */

/** One file inside a run directory. */
export interface Artifact {
  path: string;
  size: number;
  text: boolean;
}

/** `GET /api/runs/{id}/artifacts`. */
export interface ArtifactList {
  artifacts: Artifact[];
  run_dir: string;
  run_id: string;
}

/** A headline figure for the run summary. */
export interface ResultRow {
  format?: ResultFormat;
  highlight?: boolean;
  key: string;
  label: string;
  precision?: number | null;
  unit?: string;
  value: unknown;
  [key: string]: unknown;
}

/** One per-test result. */
export interface TestRow {
  classname?: string;
  duration_s?: number | null;
  message?: string;
  name: string;
  outcome: TestOutcome;
  traceback?: string;
  [key: string]: unknown;
}

/** `verdict.json`. */
export interface Verdict {
  abort_reason: string;
  aborted: boolean;
  duration_s: number;
  ended_at_utc: string;
  failures: number;
  passed: boolean;
  reason: string;
  results: ResultRow[];
  started_at_utc: string;
  stopped_early: boolean;
  successes: number;
  tests: TestRow[];
  total_iterations: number;
  [key: string]: unknown;
}

/** A named step inside one iteration. */
export interface PhaseEntry {
  detail: Record<string, unknown>;
  elapsed_s: number;
  error: string | null;
  name: string;
  success: boolean;
  [key: string]: unknown;
}

/** One line of `metrics.jsonl`. */
export interface MetricsRecord {
  /** Present on an `anomaly` record. */
  anomaly_kind?: string;
  /** Present on an `anomaly` record. */
  detail?: unknown;
  elapsed_run_s?: number | null;
  iteration?: number | null;
  kind?: MetricsKind;
  metrics?: Record<string, unknown>;
  phases?: PhaseEntry[];
  /** Present on an `anomaly` record. */
  probe?: string;
  reason?: string;
  success?: boolean | null;
  timestamp: number;
  [key: string]: unknown;
}

/** `GET /api/runs/{id}/metrics`. */
export interface MetricsResponse {
  count: number;
  records: MetricsRecord[];
  run_id: string;
}

/** `manifest.json`. Provenance for one run. */
export interface RunManifest {
  command_line: string[];
  cwd: string;
  env: Record<string, string>;
  hardware: Record<string, Record<string, string>>;
  hostname: string;
  platform: string;
  profile_path: string | null;
  profile_summary: Record<string, string>;
  python_version: string;
  repo_branch: string | null;
  repo_dirty: boolean;
  repo_sha: string | null;
  run_id: string;
  started_at_utc: string;
  suite: string;
  target: string | null;
  unit_serial: string | null;
  versions: Record<string, string>;
  [key: string]: unknown;
}

/* -------------------------------------------------------------------------
 * Run events (SSE)
 * ---------------------------------------------------------------------- */

/** Fields every published event carries. */
export interface RunEventBase {
  seq: number;
  ts: number;
}

/** A lifecycle transition. */
export interface RunStatusEvent extends RunEventBase {
  argv?: string[];
  duration_s?: number;
  exit_code?: number;
  message?: string;
  profile?: string | null;
  run_dir?: string;
  status: RunStatus;
  target?: string | null;
  type: "status";
  unit_serial?: string | null;
}

/** One captured stdout line. */
export interface RunLogEvent extends RunEventBase {
  level: LogLevel;
  message: string;
  type: "log";
}

/** Flattened numeric metrics from one record. */
export interface RunMetricsEvent extends RunEventBase {
  elapsed_s: number | null;
  iteration: number | null;
  type: "metrics";
  values: Record<string, number>;
}

/** One phase inside an iteration. */
export interface RunPhaseEvent extends RunEventBase {
  detail: Record<string, unknown>;
  elapsed_s: number;
  iteration: number | null;
  phase: string;
  success: boolean;
  type: "phase";
}

/** One completed iteration. */
export interface RunIterationEvent extends RunEventBase {
  elapsed_run_s: number | null;
  images: string[];
  iteration: number | null;
  reason: string;
  success: boolean;
  type: "iteration";
}

/** A probe reading outside its expected envelope. */
export interface RunAnomalyEvent extends RunEventBase {
  anomaly_kind: string;
  detail: unknown;
  probe: string;
  type: "anomaly";
}

/** The final outcome. */
export interface RunVerdictEvent extends RunEventBase {
  reason: string;
  result: RunVerdictCode;
  summary: Partial<Verdict>;
  type: "verdict";
}

/** Sent once when the stream closes. Carries no sequence number. */
export interface RunEndEvent {
  run_id: string;
  seq?: number;
  ts?: number;
  type: "end";
}

/** Any frame delivered on `GET /api/runs/{id}/events`. */
export type RunEvent =
  | RunAnomalyEvent
  | RunEndEvent
  | RunIterationEvent
  | RunLogEvent
  | RunMetricsEvent
  | RunPhaseEvent
  | RunStatusEvent
  | RunVerdictEvent;

/* -------------------------------------------------------------------------
 * Capabilities and instruments
 * ---------------------------------------------------------------------- */

/**
 * One registered capability provider.
 *
 * The registry stringifies every value, so `available` is `"true"` or
 * `"false"` rather than a boolean.
 */
export interface Capability {
  available: string;
  instance_id: string;
  name: string;
  [key: string]: string;
}

/**
 * One argument of an instrument command.
 *
 * Providers describe their own fields, so the form is rendered by looping over
 * these rather than by knowing anything about the instrument.
 */
export interface InstrumentField {
  choices: string[];
  label: string;
  max: number | null;
  min: number | null;
  name: string;
  type: OverrideType;
  unit: string;
}

/** One command an instrument accepts. */
export interface InstrumentCommand {
  /** The command energises something, so the button carries a warning tint. */
  danger?: boolean;
  fields: InstrumentField[];
  label: string;
  name: string;
}

/** Whether the display burns a readout large or puts it in the row beneath. */
export type ReadoutRole = "headline" | "summary";

/**
 * One state value a provider asks the panel to draw, and how.
 *
 * The provider chooses the layout; the panel only reads these fields. An
 * instrument that declares none falls back to listing every state value.
 */
export interface InstrumentReadout {
  /** Section this reading belongs to, such as a channel. Empty for ungrouped. */
  group: string;
  /** Dotted path into the instrument's `state`. */
  key: string;
  label: string;
  /** Decimal places for a numeric reading. Null leaves the number as it came. */
  precision: number | null;
  role: ReadoutRole;
  unit: string;
}

/**
 * An instrument Gauntlet owns and drives on a suite's behalf.
 *
 * `connection`, `primary_command` and `readouts` are optional because a
 * provider need not declare how it is presented; the panel falls back to the
 * plain key and value listing when they are absent.
 */
export interface Instrument {
  available: boolean;
  commands: InstrumentCommand[];
  /** How the instrument is attached, shown in the panel subtitle. */
  connection?: string;
  description: string;
  /** Id of the run driving this instrument. Empty when nothing holds it. */
  in_use_by?: string;
  instance_id: string;
  kind: string;
  name: string;
  /** Name of the command the panel gives its full width to. */
  primary_command?: string;
  readouts?: InstrumentReadout[];
  state: Record<string, unknown>;
  /** Why the provider reports itself unusable. Empty when it is available. */
  unavailable_reason: string;
}

/** `GET /api/instruments` and `POST /api/instruments/scan`. */
export interface InstrumentList {
  instruments: Instrument[];
}

/** `POST /api/instruments/{name}/command`. */
export interface InstrumentCommandResult {
  result: Record<string, unknown>;
  state: Record<string, unknown>;
}

/* -------------------------------------------------------------------------
 * Units and notes
 * ---------------------------------------------------------------------- */

/** The most recent run recorded against a unit. */
export interface UnitLastRun {
  ended_at: string | null;
  run_id: string;
  status: RunStatus;
  suite: string;
}

/** One unit under test, aggregated from the runs index. */
export interface Unit {
  failed: number;
  first_seen: string | null;
  last_run: UnitLastRun | null;
  last_seen: string | null;
  note_count: number;
  passed: number;
  run_count: number;
  serial: string;
}

/** `GET /api/units/{serial}`. */
export interface UnitDetail extends Unit {
  notes: Note[];
}

/** `GET /api/units`. */
export interface UnitList {
  total: number;
  units: Unit[];
}

/** What every `DELETE` answers with: the identifier from the path, and that it went. */
export interface Deleted {
  deleted: boolean;
  id: string;
}

/** `DELETE /api/units/{serial}`. */
export interface ForgottenUnit extends Deleted {
  /** How many of the unit's runs went with it. Zero unless `runs` was set. */
  deleted_runs: number;
}

/** `GET /api/units/{serial}/history`. */
export interface UnitHistory {
  runs: RunRow[];
  total: number;
}

/** An operator note attached to a unit or a run. */
export interface Note {
  author: string | null;
  body: string;
  created_at: string;
  id: number;
}

/** `GET /api/units/{serial}/notes` and `GET /api/runs/{id}/notes`. */
export interface NoteList {
  notes: Note[];
}

/* -------------------------------------------------------------------------
 * System
 * ---------------------------------------------------------------------- */

/** `GET /api/health`. */
export interface Health {
  status: string;
}

/** `GET /api/settings`. */
export interface Settings {
  data_dir: string;
  default_target: string;
  host: string;
  log_level: string;
  open_browser: boolean;
  port: number;
  profiles_dir: string;
  profiles_dir_override: string | null;
  runs_dir: string;
  runs_dir_override: string | null;
  runs_index_path: string;
  suite_roots: string[];
  [key: string]: unknown;
}

/**
 * `GET /api/system/info`. Static host facts.
 *
 * Every reader answers null where the kernel does not offer the file, so a
 * container or a platform without `/proc` still returns a well-formed body.
 */
export interface SystemInfo {
  arch: string | null;
  boot_time: string | null;
  contract_version: number;
  cpu_count: number | null;
  cpu_model: string | null;
  gauntlet: string;
  gauntlet_sdk: string;
  hostname: string | null;
  kernel: string | null;
  memory_total_bytes: number | null;
  os: string;
  python: string;
}

/** Physical memory, in bytes plus a percentage. */
export interface SystemMemory {
  available: number | null;
  percent: number | null;
  total: number | null;
  used: number | null;
}

/** Swap usage, in bytes plus a percentage. */
export interface SystemSwap {
  percent: number | null;
  total: number | null;
  used: number | null;
}

/** One mounted filesystem. */
export interface SystemDisk {
  free: number;
  mount: string;
  percent: number;
  total: number;
  used: number;
}

/** One thermal sensor reading. */
export interface SystemTemperature {
  celsius: number;
  label: string;
}

/**
 * `GET /api/system/data`. Sampled host telemetry.
 *
 * `cpu_percent` is null on the first call: percentages come from the
 * difference between two readings of `/proc/stat`, and the first request has
 * nothing to compare against.
 */
export interface SystemData {
  cpu_percent: number | null;
  cpu_per_core: number[];
  disks: SystemDisk[];
  /** One, five, and fifteen minute load averages, or null where the host has none. */
  load_avg: number[] | null;
  memory: SystemMemory;
  process_count: number | null;
  swap: SystemSwap;
  temperatures: SystemTemperature[];
  uptime_s: number | null;
}

/** `GET /api/schemas`. */
export interface SchemaList {
  schemas: string[];
}
