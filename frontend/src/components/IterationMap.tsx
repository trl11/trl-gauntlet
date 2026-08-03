import clsx from "clsx";
import { useMemo, useState } from "react";

import { formatDuration } from "../utils/format";
import type { IterationRow } from "./IterationTable";

import "./IterationMap.scss";

/** How far from a window edge the tooltip is allowed to sit. */
const TOOLTIP_MARGIN = 150;

/** One completed phase inside an iteration. */
export interface PhaseRow {
  detail: Record<string, unknown>;
  elapsed_s: number;
  iteration: number | null;
  phase: string;
  success: boolean;
}

/** One square: an iteration, with the phases reported inside it. */
export interface IterationCell {
  duration_s: number | null;
  iteration: number | null;
  phases: PhaseRow[];
  reason: string;
  success: boolean;
}

/** Props for {@link IterationMap}. */
export interface IterationMapProps {
  /** Iterations in the order they completed. */
  iterations: IterationRow[];
  /** Phases in the order they were reported, grouped onto their iteration. */
  phases: PhaseRow[];
  /** Called with the iteration number when a square is clicked. */
  onSelect?: (iteration: number) => void;
}

/** Where the tooltip is drawn, in viewport coordinates. */
interface Hover {
  cell: IterationCell;
  left: number;
  top: number;
}

/**
 * One cell per iteration.
 *
 * `elapsed_run_s` counts from the start of the run, so an iteration's own
 * duration is the gap to the iteration before it. Phases reported against no
 * iteration, and phases of an iteration that has not finished, become cells of
 * their own so nothing reported is dropped.
 */
function toCells(iterations: IterationRow[], phases: PhaseRow[]): IterationCell[] {
  const byIteration = new Map<number | null, PhaseRow[]>();
  for (const phase of phases) {
    const found = byIteration.get(phase.iteration);
    if (found) found.push(phase);
    else byIteration.set(phase.iteration, [phase]);
  }

  const cells: IterationCell[] = [];
  let previous = 0;
  for (const row of iterations) {
    const elapsed = row.elapsed_run_s;
    cells.push({
      duration_s: elapsed == null ? null : elapsed - previous,
      iteration: row.iteration,
      phases: byIteration.get(row.iteration) ?? [],
      reason: row.reason,
      success: row.success,
    });
    if (elapsed != null) previous = elapsed;
    byIteration.delete(row.iteration);
  }

  for (const [iteration, rows] of byIteration) {
    cells.push({
      duration_s: rows.reduce((sum, row) => sum + Math.max(row.elapsed_s, 0), 0),
      iteration,
      phases: rows,
      reason: "",
      success: rows.every((row) => row.success),
    });
  }
  return cells;
}

/** The headline a square reports on hover, and to a screen reader. */
function describe(cell: IterationCell): string {
  const parts = [
    cell.iteration == null ? "run" : `#${cell.iteration}`,
    cell.success ? "passed" : "failed",
  ];
  if (cell.duration_s != null) parts.push(formatDuration(cell.duration_s));
  if (cell.reason) parts.push(cell.reason);
  return parts.join(" · ");
}

/** Each phase of an iteration with its own duration. */
function describePhases(cell: IterationCell): string {
  return cell.phases
    .map((phase) => `${phase.phase} ${formatDuration(phase.elapsed_s)}`)
    .join(" · ");
}

/**
 * Every iteration of a run as one small square, green when it passed and red
 * when it did not. A run of thousands stays on one screen; hovering a square
 * summarises it and clicking one opens it in the iterations table.
 */
export const IterationMap: React.FC<IterationMapProps> = ({ iterations, onSelect, phases }) => {
  const cells = useMemo(() => toCells(iterations, phases), [iterations, phases]);
  const [hover, setHover] = useState<Hover | null>(null);

  if (cells.length === 0) {
    return <p className="iteration-map__empty">No iterations have been reported for this run.</p>;
  }

  const failures = cells.filter((cell) => !cell.success).length;

  const show = (cell: IterationCell, target: HTMLElement) => {
    const rect = target.getBoundingClientRect();
    setHover({
      cell,
      left: Math.min(
        Math.max(rect.left + rect.width / 2, TOOLTIP_MARGIN),
        window.innerWidth - TOOLTIP_MARGIN
      ),
      top: rect.bottom + 8,
    });
  };

  return (
    <section className="iteration-map" aria-label="Iterations">
      <p className="iteration-map__count">{`${cells.length} iterations, ${failures} failed`}</p>

      <div className="iteration-map__grid">
        {cells.map((cell, index) => (
          <button
            aria-label={describe(cell)}
            className={clsx(
              "iteration-map__cell",
              cell.success ? "iteration-map__cell--ok" : "iteration-map__cell--failed"
            )}
            key={`${cell.iteration}-${index}`}
            onBlur={() => setHover(null)}
            onClick={() => cell.iteration != null && onSelect?.(cell.iteration)}
            onFocus={(event) => show(cell, event.currentTarget)}
            onMouseEnter={(event) => show(cell, event.currentTarget)}
            onMouseLeave={() => setHover(null)}
            type="button"
          />
        ))}
      </div>

      {hover && (
        <div
          className="iteration-map__tooltip"
          role="tooltip"
          style={{ left: hover.left, top: hover.top }}
        >
          <span className="iteration-map__tooltip-head">{describe(hover.cell)}</span>
          {hover.cell.phases.length > 0 && <span>{describePhases(hover.cell)}</span>}
        </div>
      )}
    </section>
  );
};

export default IterationMap;
