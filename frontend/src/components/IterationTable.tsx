import { faXmark } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Badge, Button, Checkbox, Tooltip } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useId, useMemo, useRef, useState } from "react";

import usePersistedSeries from "@hooks/usePersistedSeries";
import { formatDuration, formatNumber } from "../utils/format";
import { naturalCompare } from "../utils/metrics";
import type { MetricSample } from "./MetricsChart";
import SeriesPicker from "./SeriesPicker";

import "./IterationTable.scss";

/** Value columns shown before the operator picks their own, when the suite declares none. */
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
  /** Run this table belongs to, to scope the persisted column pick. */
  runId: string;
  /** Iterations in the order they completed. */
  iterations: IterationRow[];
  /** Metric samples, used to show the values recorded against each iteration. */
  samples: MetricSample[];
  /** Iteration to scroll to and mark, set when one is opened from elsewhere. */
  selected?: number | null;
  /**
   * Series the suite declares as worth showing by default, in
   * `suite.yaml`'s `default_metrics`. Only those the run actually reported
   * are used; falls back to the first few reported series when none apply.
   */
  defaultMetrics: string[];
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
  runId,
  iterations,
  samples,
  selected = null,
  defaultMetrics,
}) => {
  const fieldId = useId();
  const [failuresOnly, setFailuresOnly] = useState(false);
  const [chosenColumns, setChosenColumns] = usePersistedSeries(
    `gauntlet:run:${runId}:iteration-columns`
  );
  const rows = useRef(new Map<number, HTMLTableRowElement>());

  const values = useMemo(() => valuesByIteration(samples), [samples]);
  const columnNames = useMemo(() => {
    const seen = new Set<string>();
    for (const sample of values.values()) {
      for (const name of Object.keys(sample)) seen.add(name);
    }
    return [...seen].sort(naturalCompare);
  }, [values]);
  const reported = defaultMetrics.filter((name) => columnNames.includes(name));
  const columns =
    chosenColumns ?? (reported.length > 0 ? reported : columnNames.slice(0, DEFAULT_COLUMNS));
  const removeColumn = (name: string) =>
    setChosenColumns(columns.filter((entry) => entry !== name));

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
        {columnNames.length > 0 && (
          <SeriesPicker names={columnNames} selected={columns} onChange={setChosenColumns} />
        )}
        <span className="iteration-table__count">
          {iterations.length} iterations, {failures} failed
        </span>
        <Checkbox
          id={`${fieldId}-failures`}
          label="Failures only"
          checked={failuresOnly}
          onChange={(event) => setFailuresOnly(event.target.checked)}
        />
      </div>

      <div className="iteration-table__scroll">
        <table className="iteration-table__table">
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Result</th>
              <th scope="col">Duration</th>
              <th scope="col">Reason</th>
              {columns.map((name) => (
                <th
                  className="iteration-table__mono iteration-table__column-head"
                  key={name}
                  scope="col"
                >
                  <span className="iteration-table__column-head-inner">
                    {name}
                    <Tooltip content="Remove column">
                      <Button
                        className="iteration-table__remove-column"
                        size="small"
                        square
                        color="transparent"
                        aria-label={`Remove ${name} column`}
                        onClick={() => removeColumn(name)}
                      >
                        <FontAwesomeIcon icon={faXmark} />
                      </Button>
                    </Tooltip>
                  </span>
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
