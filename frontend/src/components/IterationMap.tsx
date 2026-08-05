import { Tooltip } from "@trl11/components/ui";
import clsx from "clsx";
import { useMemo } from "react";

import { formatDuration } from "../utils/format";
import type { IterationRow } from "./IterationTable";

import "./IterationMap.scss";

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

/** What one square burns: it passed, it passed with something to say, it failed. */
type CellState = "failed" | "ok" | "warned";

/**
 * The state of one square.
 *
 * An iteration the suite recorded as a pass is still worth flagging when
 * something inside it did not pass, or when the suite recorded a reason
 * alongside the pass — which is how a skipped or degraded iteration reaches
 * us, the contract carrying no outcome for an iteration beyond `success`.
 */
function stateOf(cell: IterationCell): CellState {
  if (!cell.success) return "failed";
  if (cell.reason || cell.phases.some((phase) => !phase.success)) return "warned";
  return "ok";
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

/** How one square's state reads in its summary. */
const STATE_WORDS: Record<CellState, string> = {
  failed: "failed",
  ok: "passed",
  warned: "passed with warnings",
};

/** The headline a square reports on hover, and to a screen reader. */
function describe(cell: IterationCell): string {
  const parts = [cell.iteration == null ? "run" : `#${cell.iteration}`, STATE_WORDS[stateOf(cell)]];
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

/** The tally above the squares, naming only the states that occurred. */
function tally(cells: IterationCell[]): string {
  const failed = cells.filter((cell) => stateOf(cell) === "failed").length;
  const warned = cells.filter((cell) => stateOf(cell) === "warned").length;
  const counts = [`${cells.length} iterations`, `${failed} failed`];
  if (warned > 0) counts.push(`${warned} warned`);
  return counts.join(", ");
}

/**
 * Every iteration of a run as one small square: green when it passed, yellow
 * when it passed with something to report, red when it did not. A run of
 * thousands stays on one screen; hovering a square summarises it and clicking
 * one opens it in the iterations table.
 */
export const IterationMap: React.FC<IterationMapProps> = ({ iterations, onSelect, phases }) => {
  const cells = useMemo(() => toCells(iterations, phases), [iterations, phases]);

  if (cells.length === 0) {
    return <p className="iteration-map__empty">No iterations have been reported for this run.</p>;
  }

  return (
    <section className="iteration-map" aria-label="Iterations">
      <p className="iteration-map__count">{tally(cells)}</p>

      <div className="iteration-map__grid">
        {cells.map((cell, index) => (
          <Tooltip
            content={
              <span className="iteration-map__tooltip">
                <span className="iteration-map__tooltip-head">{describe(cell)}</span>
                {cell.phases.length > 0 && <span>{describePhases(cell)}</span>}
              </span>
            }
            key={`${cell.iteration}-${index}`}
          >
            <button
              aria-label={describe(cell)}
              className={clsx("iteration-map__cell", `iteration-map__cell--${stateOf(cell)}`)}
              onClick={() => cell.iteration != null && onSelect?.(cell.iteration)}
              type="button"
            />
          </Tooltip>
        ))}
      </div>
    </section>
  );
};

export default IterationMap;
