import { DataField } from "@trl11/components/ui";
import clsx from "clsx";

import type { ResultRow, Verdict } from "@api/types";
import { formatBytes, formatDuration, formatNumber, formatPercent } from "../utils/format";

import "./VerdictSummary.scss";

/** Props for {@link VerdictSummary}. */
export interface VerdictSummaryProps {
  /** Rendered above the stat fields, below the empty-verdict message when there is one. */
  children?: React.ReactNode;
  /** The suite's own rollup, usually `summary.md`. */
  summaryText?: string | null;
  /** `verdict.json`, or the partial summary the verdict event carries. */
  verdict: Partial<Verdict> | null;
}

/** Render one headline figure the way the suite asked for. */
function formatResult(row: ResultRow): string {
  const value = row.value;
  if (typeof value !== "number") return value == null ? "-" : String(value);
  switch (row.format) {
    case "bytes":
      return formatBytes(value);
    case "duration":
      return formatDuration(value);
    case "int":
      return formatNumber(value, 0);
    case "percent":
      return formatPercent(value);
    case "decimal":
      return formatNumber(value, row.precision ?? undefined);
    default:
      return String(value);
  }
}

/** The run outcome, its counters, and whatever headline figures the suite set. */
export const VerdictSummary: React.FC<VerdictSummaryProps> = ({
  children,
  summaryText,
  verdict,
}) => {
  if (verdict === null) {
    return (
      <section className="verdict-summary" aria-label="Verdict details">
        <p className="verdict-summary__empty">No verdict has been written for this run.</p>
        {children}
      </section>
    );
  }

  const results = verdict.results ?? [];

  return (
    <section className="verdict-summary" aria-label="Verdict details">
      {children}

      <div className="verdict-summary__fields">
        <DataField label="Iterations" value={formatNumber(verdict.total_iterations ?? 0, 0)} />
        <DataField
          label="Successes"
          value={formatNumber(verdict.successes ?? 0, 0)}
          color="green"
        />
        <DataField label="Failures" value={formatNumber(verdict.failures ?? 0, 0)} color="red" />
        <DataField label="Duration" value={formatDuration(verdict.duration_s)} />
        {verdict.stopped_early && <DataField label="Stopped early" value="yes" color="yellow" />}
        {verdict.aborted && (
          <DataField label="Aborted" value={verdict.abort_reason || "yes"} color="red" />
        )}
      </div>

      {results.length > 0 && (
        <div className="verdict-summary__results">
          {results.map((row) => (
            <div
              className={clsx(
                "verdict-summary__figure",
                row.highlight && "verdict-summary__figure--highlight"
              )}
              key={row.key}
            >
              <span className="verdict-summary__label">{row.label || row.key}</span>
              <span className="verdict-summary__value">
                {formatResult(row)}
                {row.unit && <span className="verdict-summary__unit"> {row.unit}</span>}
              </span>
            </div>
          ))}
        </div>
      )}

      {summaryText && <pre className="verdict-summary__text">{summaryText}</pre>}
    </section>
  );
};

export default VerdictSummary;
