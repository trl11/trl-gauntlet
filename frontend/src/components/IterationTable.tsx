import { Badge, Checkbox } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import { formatDuration, formatNumber } from "../utils/format";
import { naturalCompare } from "../utils/metrics";
import type { MetricSample } from "./MetricsChart";
import SeriesPicker from "./SeriesPicker";

import "./IterationTable.scss";

/** Value columns shown before the operator picks their own. */
const DEFAULT_COLUMNS = 3;

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
  /** Iteration to scroll to and mark, set when one is opened from elsewhere. */
  selected?: number | null;
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
export const IterationTable: React.FC<IterationTableProps> = ({
  iterations,
  samples,
  selected = null,
}) => {
  const fieldId = useId();
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [chosenColumns, setChosenColumns] = useState<string[] | null>(null);
  const rows = useRef(new Map<number, HTMLTableRowElement>());

  const values = useMemo(() => valuesByIteration(samples), [samples]);
  const columnNames = useMemo(() => {
    const seen = new Set<string>();
    for (const sample of values.values()) {
      for (const name of Object.keys(sample)) seen.add(name);
    }
    return [...seen].sort(naturalCompare);
  }, [values]);
  const columns = chosenColumns ?? columnNames.slice(0, DEFAULT_COLUMNS);

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

  useEffect(() => {
    if (selected == null) return;
    rows.current.get(selected)?.scrollIntoView({ block: "center" });
  }, [failuresOnly, iterations, selected]);

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

      {columnNames.length > 0 && (
        <SeriesPicker names={columnNames} selected={columns} onChange={setChosenColumns} />
      )}

      <div className="iteration-table__scroll">
        <table className="iteration-table__table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Result</th>
              <th scope="col">Duration</th>
              <th scope="col">Reason</th>
              {columns.map((name) => (
                <th className="iteration-table__mono" key={name} scope="col">
                  {name}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {shown.map(({ duration, row }, index) => {
              const sample = row.iteration == null ? undefined : values.get(row.iteration);
              return (
                <tr
                  className={clsx(row.iteration === selected && "is-selected")}
                  key={`${row.iteration}-${index}`}
                  ref={(node) => {
                    if (row.iteration == null) return;
                    if (node) rows.current.set(row.iteration, node);
                    else rows.current.delete(row.iteration);
                  }}
                >
                  <td className="iteration-table__mono">{row.iteration ?? "-"}</td>
                  <td>
                    <Badge color={row.success ? "green" : "red"}>
                      {row.success ? "PASS" : "FAIL"}
                    </Badge>
                  </td>
                  <td className="iteration-table__mono">{formatDuration(duration)}</td>
                  <td className="iteration-table__reason">{row.reason || "-"}</td>
                  {columns.map((name) => (
                    <td className="iteration-table__mono" key={name}>
                      {sample && name in sample ? formatNumber(sample[name]) : "-"}
                    </td>
                  ))}
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
