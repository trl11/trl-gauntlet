/** Which run statuses mean the run has not finished. */

import type { RunStatus } from "@api/types";

/** The four statuses a run holds while it is still in flight. */
export const LIVE_STATUSES: RunStatus[] = ["aborting", "running", "starting", "stopping"];

/** Is this run still in flight. */
export function isLive(status: string | null | undefined): boolean {
  return LIVE_STATUSES.some((live) => live === status);
}

/**
 * The one status vocabulary every run filter offers, so a run in flight
 * reads as "In flight" whether it's filtered client-side (RunTable) or by
 * the server (HistoryPage).
 */
export const RUN_STATUS_OPTIONS = [
  { value: "all", label: "Any status" },
  { value: "passed", label: "Passed" },
  { value: "failed", label: "Failed" },
  { value: "aborted", label: "Aborted" },
  { value: "error", label: "Error" },
  { value: "live", label: "In flight" },
];

/** Does this run match a {@link RUN_STATUS_OPTIONS} value. */
export function matchesStatus(status: string | null | undefined, filter: string): boolean {
  if (filter === "all") return true;
  if (filter === "live") return isLive(status);
  return status === filter;
}
