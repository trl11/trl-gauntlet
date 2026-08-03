import { Badge, Checkbox } from "@trl11/components/ui";
import { useId, useMemo, useState } from "react";

import { formatDuration, formatNumber } from "../utils/format";
import type { MetricSample } from "./MetricsChart";

import "./IterationTable.scss";

/** One completed iteration. */
export interface IterationRow {
  elapsed_run_s: number | null;
  images: string[];
  iteration: number | null;
  reason: string;
  success: boolean;
}

/** Props for {@link IterationTable}. */
export interface IterationTableProps {
  /** Iterations in the order they completed. */
  iterations: IterationRow[];
  /** Metric samples, used to show the values recorded against each iteration. */
  samples: MetricSample[];
}

/** The last sample reported for each iteration number. */
function valuesByIteration(samples: MetricSample[]): Map<number, Record<string, number>> {
  const found = new Map<number, Record<string, number>>();
  for (const sample of samples) {
    if (sample.iteration == null) continue;
    found.set(sample.iteration, sample.values);
  }
  return found;
}

/**
 * Per-iteration results.
 *
 * `elapsed_run_s` counts from the start of the run, so one iteration's own
 * duration is the gap to the iteration before it.
 */
export const IterationTable: React.FC<IterationTableProps> = ({ iterations, samples }) => {
  const fieldId = useId();
  const [failuresOnly, setFailuresOnly] = useState(false);

  const values = useMemo(() => valuesByIteration(samples), [samples]);
  const timed = useMemo(
    () =>
      iterations.map((row, index) => {
        const previous = index > 0 ? (iterations[index - 1].elapsed_run_s ?? 0) : 0;
        return { duration: row.elapsed_run_s == null ? null : row.elapsed_run_s - previous, row };
      }),
    [iterations]
  );
  const shown = failuresOnly ? timed.filter((entry) => !entry.row.success) : timed;
  const failures = iterations.filter((row) => !row.success).length;

  if (iterations.length === 0) {
    return <p className="iteration-table__empty">No iterations have completed yet.</p>;
  }

  return (
    <section className="iteration-table" aria-label="Iterations">
      <div className="iteration-table__controls">
        <Checkbox
          id={`${fieldId}-failures`}
          label="Failures only"
          checked={failuresOnly}
          onChange={(event) => setFailuresOnly(event.target.checked)}
        />
        <span className="iteration-table__count">
          {iterations.length} iterations, {failures} failed
        </span>
      </div>

      <div className="iteration-table__scroll">
        <table className="iteration-table__table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Result</th>
              <th scope="col">Duration</th>
              <th scope="col">Reason</th>
              <th scope="col">Values</th>
            </tr>
          </thead>
          <tbody>
            {shown.map(({ duration, row }, index) => {
              const sample = row.iteration == null ? undefined : values.get(row.iteration);
              return (
                <tr key={`${row.iteration}-${index}`}>
                  <td className="iteration-table__mono">{row.iteration ?? "-"}</td>
                  <td>
                    <Badge color={row.success ? "green" : "red"}>
                      {row.success ? "PASS" : "FAIL"}
                    </Badge>
                  </td>
                  <td className="iteration-table__mono">{formatDuration(duration)}</td>
                  <td className="iteration-table__reason">{row.reason || "-"}</td>
                  <td>
                    <div className="iteration-table__values">
                      {Object.entries(sample ?? {})
                        .sort(([left], [right]) => left.localeCompare(right))
                        .map(([name, value]) => (
                          <span className="iteration-table__chip" key={name}>
                            {name} {formatNumber(value)}
                          </span>
                        ))}
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {shown.length === 0 && <p className="iteration-table__empty">Every iteration passed.</p>}
    </section>
  );
};

export default IterationTable;
