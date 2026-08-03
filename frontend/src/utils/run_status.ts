/** Which run statuses mean the run has not finished. */

import type { RunStatus } from "@api/types";

/** The four statuses a run holds while it is still in flight. */
export const LIVE_STATUSES: RunStatus[] = ["aborting", "running", "starting", "stopping"];

/** Is this run still in flight. */
export function isLive(status: string | null | undefined): boolean {
  return LIVE_STATUSES.some((live) => live === status);
}
