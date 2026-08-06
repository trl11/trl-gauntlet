/**
 * What each run column is called and how one cell of it renders.
 *
 * Kept beside {@link RunTable} so the table file is only about the table.
 */

import { Link } from "react-router";

import type { RunRow } from "@api/types";
import StatusPill from "@components/StatusPill";
import { formatDuration, formatTimestamp } from "../utils/format";

/** Columns {@link RunTable} knows how to render. */
export type RunTableColumn =
  | "campaign"
  | "duration_s"
  | "fail_reason"
  | "profile"
  | "run_id"
  | "started_at"
  | "status"
  | "suite"
  | "target"
  | "unit_serial";

/** Header text and alignment for one column. */
export interface ColumnSpec {
  align?: "right";
  header: string;
  sortable: boolean;
}

export const COLUMNS: Record<RunTableColumn, ColumnSpec> = {
  // Not sortable: it is resolved per request from the suite, not a column of
  // the runs table, so there is nothing for the index to order by.
  campaign: { header: "Campaign", sortable: false },
  duration_s: { header: "Duration", sortable: true, align: "right" },
  fail_reason: { header: "Reason", sortable: false },
  profile: { header: "Profile", sortable: true },
  run_id: { header: "Run", sortable: true },
  started_at: { header: "Started", sortable: true },
  status: { header: "Status", sortable: true },
  suite: { header: "Suite", sortable: true },
  target: { header: "Target", sortable: true },
  unit_serial: { header: "Unit", sortable: true },
};

/** Column order used when the caller does not choose one. */
export const DEFAULT_RUN_COLUMNS: RunTableColumn[] = [
  "status",
  "suite",
  "run_id",
  "profile",
  "unit_serial",
  "started_at",
  "duration_s",
];

/** Order two runs by one column, nulls last. */
export function compare(a: RunRow, b: RunRow, column: RunTableColumn): number {
  const left = a[column];
  const right = b[column];
  if (left === right) return 0;
  if (left === null || left === undefined) return 1;
  if (right === null || right === undefined) return -1;
  if (typeof left === "number" && typeof right === "number") return left - right;
  return String(left).localeCompare(String(right));
}

/** Does a run match a lowercased search term across its text fields. */
export function matches(run: RunRow, needle: string): boolean {
  if (!needle) return true;
  const haystack = [
    run.run_id,
    run.suite,
    run.profile,
    run.target,
    run.unit_serial,
    run.status,
    run.fail_reason,
    run.campaign?.title,
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(needle);
}

/** One table cell. */
export function renderCell(run: RunRow, column: RunTableColumn): React.ReactNode {
  switch (column) {
    case "status":
      return <StatusPill status={run.status} verdict={run.verdict} />;
    case "run_id":
      return <span className="run-table__mono">{run.run_id}</span>;
    case "started_at":
      return formatTimestamp(run.started_at);
    case "duration_s":
      return formatDuration(run.duration_s);
    case "campaign":
      // The row opens the run, so the campaign link must not let that click
      // through.
      return run.campaign ? (
        <Link
          to={`/tests?view=campaigns&campaign=${encodeURIComponent(run.campaign.key)}`}
          onClick={(event) => event.stopPropagation()}
        >
          {run.campaign.title}
        </Link>
      ) : (
        "-"
      );
    case "fail_reason":
      return <span className="run-table__reason">{run.fail_reason || "-"}</span>;
    case "unit_serial":
      // The row opens the run, so the unit link must not let that click through.
      return run.unit_serial ? (
        <Link
          to={`/units/${encodeURIComponent(run.unit_serial)}`}
          onClick={(event) => event.stopPropagation()}
        >
          {run.unit_serial}
        </Link>
      ) : (
        "-"
      );
    default:
      return run[column] || "-";
  }
}
