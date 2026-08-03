/** Turning selected runs into a CSV file the operator can download. */

import type { RunRow } from "@api/types";

/** Fields written to the export, in order. */
const CSV_COLUMNS: Array<keyof RunRow> = [
  "run_id",
  "suite",
  "profile",
  "status",
  "verdict",
  "unit_serial",
  "target",
  "started_at",
  "ended_at",
  "duration_s",
  "fail_reason",
];

function csvCell(value: unknown): string {
  const text = value == null ? "" : String(value);
  return /[",\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

/** A header row followed by one row per run. */
export function toCsv(runs: RunRow[]): string {
  const rows = runs.map((run) => CSV_COLUMNS.map((column) => csvCell(run[column])).join(","));
  return [CSV_COLUMNS.join(","), ...rows].join("\n");
}

/** Hand the text to the browser as a download. */
export function downloadCsv(text: string, filename = "gauntlet-runs.csv"): void {
  const url = URL.createObjectURL(new Blob([text], { type: "text/csv" }));
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
