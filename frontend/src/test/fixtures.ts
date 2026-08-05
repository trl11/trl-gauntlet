/**
 * Real API responses, captured from a running Gauntlet against the built-in
 * suites. `frontend/src/test/fixtures/api.json` was written by driving the server;
 * the run id in it was replaced with `RUN-0001`.
 *
 * Every export below is assigned to its declared API type, so `tsc` fails if a
 * captured body and `api/types.ts` ever disagree.
 */

import type {
  ArtifactList,
  Health,
  Instrument,
  InstrumentCommand,
  InstrumentList,
  JsonSchema,
  MetricsRecord,
  MetricsResponse,
  NoteList,
  OverrideType,
  ProfileContent,
  ResultRow,
  RunList,
  RunManifest,
  RunRow,
  RunStatus,
  RunVerdictCode,
  SchemaList,
  Settings,
  Suite,
  SuiteList,
  SystemData,
  SystemInfo,
  Unit,
  UnitDetail,
  UnitHistory,
  UnitList,
  Verdict,
} from "@api/types";

import captured from "./fixtures/api.json";

/** The run every run-scoped fixture belongs to. */
export const RUN_ID = "RUN-0001";

/** The unit every unit-scoped fixture belongs to. */
export const UNIT_SERIAL = "SN-BENCH-042";

/**
 * Restore a string literal a JSON import widened to `string`.
 *
 * A captured `"passed"` arrives typed as `string` and so does not satisfy
 * `RunStatus` on its own. Only the enum-like leaves below go through this;
 * every other field of every fixture is still checked by its assignment.
 */
function literal<T extends string>(value: string): T {
  return value as T;
}

/** The optional keys of `exec.args` differ per suite, which JSON inference turns
 * into a union of object shapes rather than one string map. */
function asArgs(args: object): Record<string, string> {
  const flat: Record<string, string> = {};
  for (const [key, value] of Object.entries(args)) flat[key] = String(value);
  return flat;
}

type CapturedRun = (typeof captured.runs.runs)[number];
type CapturedUnit = (typeof captured.units.units)[number];

function asRun(row: CapturedRun): RunRow {
  return {
    ...row,
    status: literal<RunStatus>(row.status),
    verdict: literal<RunVerdictCode>(row.verdict),
  };
}

function asUnit(row: CapturedUnit): Unit {
  return { ...row, last_run: { ...row.last_run, status: literal<RunStatus>(row.last_run.status) } };
}

function asSuite(entry: (typeof captured.suites.suites)[number]): Suite {
  return {
    ...entry,
    apiVersion: 1,
    overrides: entry.overrides.map((override) => ({
      ...override,
      type: literal<OverrideType>(override.type),
    })),
    produces: entry.produces.map((name) => literal<Suite["produces"][number]>(name)),
    exec: {
      ...entry.exec,
      args: asArgs(entry.exec.args),
      graceful_stop_signal: literal<Suite["exec"]["graceful_stop_signal"]>(
        entry.exec.graceful_stop_signal
      ),
    },
  };
}

function asCommand(command: (typeof captured.instruments.instruments)[number]["commands"][number]) {
  return {
    ...command,
    fields: command.fields.map((field) => ({
      ...field,
      type: literal<OverrideType>(field.type),
    })),
  } satisfies InstrumentCommand;
}

function asInstrument(entry: (typeof captured.instruments.instruments)[number]): Instrument {
  return { ...entry, commands: entry.commands.map(asCommand) };
}

function asRecord(record: (typeof captured.run_metrics.records)[number]): MetricsRecord {
  return { ...record, kind: literal<MetricsRecord["kind"] & string>(record.kind) };
}

function asResult(row: (typeof captured.run_verdict.results)[number]): ResultRow {
  return { ...row, format: literal<ResultRow["format"] & string>(row.format) };
}

export const health: Health = captured.health;
export const settings: Settings = captured.settings;
export const suites: SuiteList = {
  ...captured.suites,
  suites: captured.suites.suites.map(asSuite),
};
export const suite: Suite = asSuite(captured.suite_system_stats);
export const profileSchema: JsonSchema = captured.profile_schema;
export const profile: ProfileContent = captured.profile_quick;
export const schemas: SchemaList = captured.schemas;
export const runs: RunList = { ...captured.runs, runs: captured.runs.runs.map(asRun) };
export const run: RunRow = asRun(captured.run);
export const runVerdict: Verdict = {
  ...captured.run_verdict,
  results: captured.run_verdict.results.map(asResult),
  tests: captured.run_verdict.tests.map((test) => ({
    ...test,
    outcome: literal<Verdict["tests"][number]["outcome"]>(test.outcome),
  })),
};
export const runManifest: RunManifest = captured.run_manifest;
export const runMetrics: MetricsResponse = {
  ...captured.run_metrics,
  records: captured.run_metrics.records.map(asRecord),
};
export const runArtifacts: ArtifactList = captured.run_artifacts;
export const runNotes: NoteList = captured.run_notes;
export const units: UnitList = {
  total: captured.units.total,
  units: captured.units.units.map(asUnit),
};
export const unit: UnitDetail = { ...asUnit(captured.unit), notes: captured.unit.notes };
export const unitHistory: UnitHistory = {
  ...captured.unit_history,
  runs: captured.unit_history.runs.map(asRun),
};
export const unitNotes: NoteList = captured.unit_notes;
export const instruments: InstrumentList = {
  instruments: captured.instruments.instruments.map(asInstrument),
};
export const systemInfo: SystemInfo = captured.system_info;
export const systemData: SystemData = captured.system_data;
