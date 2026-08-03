import { Badge } from "@trl11/components/ui";
import clsx from "clsx";

import type { RunStatus, RunVerdictCode } from "@api/types";
import { isLive } from "../utils/run_status";

import "./StatusPill.scss";

type BadgeColor = "amber" | "blue" | "green" | "outline" | "purple" | "red";

const COLORS: Record<string, BadgeColor> = {
  aborted: "amber",
  aborting: "amber",
  error: "purple",
  failed: "red",
  interrupted: "amber",
  passed: "green",
  running: "blue",
  starting: "blue",
  stopping: "amber",
};

/** Props for {@link StatusPill}. */
export interface StatusPillProps {
  /** Run lifecycle state. Ignored when `verdict` is set. */
  status?: RunStatus | string | null;
  /** Short verdict code. Takes precedence over `status` when present. */
  verdict?: RunVerdictCode | string | null;
}

/** Verdict codes map onto the status word they correspond to. */
const FROM_VERDICT: Record<string, string> = {
  aborted: "aborted",
  error: "error",
  fail: "failed",
  pass: "passed",
};

/** The run outcome as a coloured badge. */
export const StatusPill: React.FC<StatusPillProps> = ({ status, verdict }) => {
  const raw = (verdict || status || "").toString().trim();
  const key = raw.toLowerCase();
  const normalized = FROM_VERDICT[key] ?? key;
  const color = COLORS[normalized] ?? "outline";
  const label = raw ? raw.toUpperCase() : "UNKNOWN";

  return (
    <Badge
      color={color}
      className={clsx("status-pill", isLive(normalized) && "status-pill--live")}
      role="status"
      aria-label={`Status: ${raw || "unknown"}`}
    >
      {isLive(normalized) && <span className="status-pill__dot" aria-hidden="true" />}
      {label}
    </Badge>
  );
};

export default StatusPill;
