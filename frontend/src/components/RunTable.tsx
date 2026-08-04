import { faChevronDown, faChevronRight } from "@fortawesome/free-solid-svg-icons";
import { faSort, faSortDown, faSortUp } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { Checkbox, Input, Pagination, Select, TableSkeleton } from "@trl11/components/ui";
import clsx from "clsx";
import { Fragment, useEffect, useId, useMemo, useState } from "react";
import { useNavigate } from "react-router";

import type { RunRow } from "@api/types";
import EmptyState from "@components/EmptyState";
import RowMenu from "@components/RowMenu";
import {
  COLUMNS,
  DEFAULT_RUN_COLUMNS,
  compare,
  matches,
  renderCell,
  type RunTableColumn,
} from "@components/run_columns";
import { matchesStatus, RUN_STATUS_OPTIONS } from "../utils/run_status";

import "./RunTable.scss";

/** Which way a sortable column is ordered. */
export type SortDirection = "asc" | "desc";

/** Props for {@link RunTable}. */
export interface RunTableProps {
  /** Which columns to render, left to right. */
  columns?: RunTableColumn[];
  /** Sort order. Only read alongside `onSort`. */
  direction?: SortDirection;
  /** Shown when there is nothing to list. */
  emptyMessage?: React.ReactNode;
  /** Render the search box and status filter above the table. */
  filterable?: boolean;
  /** Render a skeleton instead of rows. */
  loading?: boolean;
  /** Offers "Delete" from a per-row "more actions" menu when set. */
  onDeleteRun?: (run: RunRow) => void;
  /** Replaces the default navigation to `/runs/:runId`. */
  onSelect?: (run: RunRow) => void;
  /** Receives the whole new selection. With `selectedIds`, renders a checkbox column. */
  onSelectionChange?: (runIds: string[]) => void;
  /**
   * Takes over ordering: the caller hands over rows already sorted, and a
   * header click reports the column and direction wanted rather than reordering.
   */
  onSort?: (column: RunTableColumn, direction: SortDirection) => void;
  /** Rows per page. Zero renders every row without pagination. */
  pageSize?: number;
  /** Detail panel for one run, revealed by a per-row expander. */
  renderExpanded?: (run: RunRow) => React.ReactNode;
  /** The runs to list. */
  runs: RunRow[];
  /** Selected run ids. With `onSelectionChange`, renders a checkbox column. */
  selectedIds?: string[];
  /** Column being sorted by. Only read alongside `onSort`. */
  sort?: RunTableColumn;
}

/** Sortable, filterable, paginated list of runs. */
export const RunTable: React.FC<RunTableProps> = ({
  columns = DEFAULT_RUN_COLUMNS,
  direction,
  emptyMessage = "No runs match these filters.",
  filterable = true,
  loading = false,
  onDeleteRun,
  onSelect,
  onSelectionChange,
  onSort,
  pageSize = 20,
  renderExpanded,
  runs,
  selectedIds,
  sort,
}) => {
  const navigate = useNavigate();
  const fieldId = useId();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState("all");
  const [ownColumn, setOwnColumn] = useState<RunTableColumn>("started_at");
  const [ownDirection, setOwnDirection] = useState<SortDirection>("desc");
  const [page, setPage] = useState(1);
  const [perPage, setPerPage] = useState(pageSize || 20);
  const [expanded, setExpanded] = useState<string | null>(null);

  // With `onSort` the caller has already ordered the rows; without it the table
  // holds the order itself and sorts what it was handed.
  const controlled = onSort !== undefined;
  const activeColumn = controlled ? sort : ownColumn;
  const activeDirection = (controlled ? direction : ownDirection) ?? "desc";
  const selectable = selectedIds !== undefined && onSelectionChange !== undefined;

  const matching = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = runs.filter(
      (run) => matches(run, needle) && matchesStatus(run.status, status)
    );
    if (controlled) return filtered;
    const sign = ownDirection === "asc" ? 1 : -1;
    return [...filtered].sort((a, b) => sign * compare(a, b, ownColumn));
  }, [runs, search, status, controlled, ownColumn, ownDirection]);

  const paginate = pageSize > 0;
  const totalPages = paginate ? Math.max(1, Math.ceil(matching.length / perPage)) : 1;

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  const rows = paginate ? matching.slice((page - 1) * perPage, page * perPage) : matching;
  const pageIds = rows.map((run) => run.run_id);
  const allSelected = rows.length > 0 && pageIds.every((id) => selectedIds?.includes(id));
  const extraColumns = (selectable ? 1 : 0) + (renderExpanded ? 1 : 0) + (onDeleteRun ? 1 : 0);

  const sortBy = (column: RunTableColumn) => {
    if (!COLUMNS[column].sortable) return;
    let next: SortDirection = column === "started_at" ? "desc" : "asc";
    if (column === activeColumn) next = activeDirection === "asc" ? "desc" : "asc";
    if (onSort) onSort(column, next);
    else {
      setOwnColumn(column);
      setOwnDirection(next);
    }
  };

  const toggleOne = (runId: string) => {
    if (!selectedIds || !onSelectionChange) return;
    onSelectionChange(
      selectedIds.includes(runId)
        ? selectedIds.filter((id) => id !== runId)
        : [...selectedIds, runId]
    );
  };

  const toggleAll = () => {
    if (!selectedIds || !onSelectionChange) return;
    if (allSelected) onSelectionChange(selectedIds.filter((id) => !pageIds.includes(id)));
    else onSelectionChange([...new Set([...selectedIds, ...pageIds])]);
  };

  const openRun = (run: RunRow) => {
    if (onSelect) onSelect(run);
    else navigate(`/runs/${encodeURIComponent(run.run_id)}`);
  };

  // The row's one real "open" affordance lives in a single cell rather than
  // on the row itself, so it never nests inside another interactive element.
  // unit_serial renders its own link, so it can't also hold the row's.
  const openColumn = columns.find((column) => column !== "unit_serial");

  return (
    <div className="run-table">
      {filterable && (
        <div className="run-table__filters">
          <Input
            id={`${fieldId}-search`}
            type="search"
            placeholder="Filter by run, suite, profile, unit"
            aria-label="Filter runs"
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              setPage(1);
            }}
          />
          <Select
            id={`${fieldId}-status`}
            aria-label="Filter by status"
            options={RUN_STATUS_OPTIONS}
            value={status}
            onChange={(event) => {
              setStatus(event.target.value);
              setPage(1);
            }}
          />
          <span className="run-table__count">{`${matching.length} of ${runs.length}`}</span>
        </div>
      )}

      {loading ? (
        <TableSkeleton rows={6} />
      ) : rows.length === 0 ? (
        <EmptyState title="Nothing to show" message={emptyMessage} />
      ) : (
        <div className="run-table__scroll">
          <table className="run-table__table">
            <thead>
              <tr>
                {selectable && (
                  <th scope="col" className="run-table__pick">
                    <Checkbox
                      id={`${fieldId}-all`}
                      aria-label="Select every run on this page"
                      checked={allSelected}
                      onChange={toggleAll}
                    />
                  </th>
                )}
                {renderExpanded && <th scope="col" aria-label="Expand" />}
                {columns.map((column) => {
                  const spec = COLUMNS[column];
                  const active = activeColumn === column;
                  return (
                    <th
                      key={column}
                      scope="col"
                      className={clsx(spec.align === "right" && "is-right")}
                      aria-sort={
                        active ? (activeDirection === "asc" ? "ascending" : "descending") : "none"
                      }
                    >
                      {spec.sortable ? (
                        <button
                          type="button"
                          className="run-table__sort"
                          onClick={() => sortBy(column)}
                        >
                          {spec.header}
                          <FontAwesomeIcon
                            icon={
                              active ? (activeDirection === "asc" ? faSortUp : faSortDown) : faSort
                            }
                            aria-hidden="true"
                          />
                        </button>
                      ) : (
                        spec.header
                      )}
                    </th>
                  );
                })}
                {onDeleteRun && <th scope="col" aria-label="Actions" />}
              </tr>
            </thead>
            <tbody>
              {rows.map((run) => (
                <Fragment key={run.run_id}>
                  <tr>
                    {selectable && (
                      <td className="run-table__pick">
                        <Checkbox
                          id={`${fieldId}-pick-${run.run_id}`}
                          aria-label={`Select run ${run.run_id}`}
                          checked={selectedIds.includes(run.run_id)}
                          onChange={() => toggleOne(run.run_id)}
                        />
                      </td>
                    )}
                    {renderExpanded && (
                      <td>
                        <button
                          type="button"
                          className="run-table__expand"
                          aria-expanded={expanded === run.run_id}
                          aria-label={`Details for run ${run.run_id}`}
                          onClick={() =>
                            setExpanded((current) => (current === run.run_id ? null : run.run_id))
                          }
                        >
                          <FontAwesomeIcon
                            icon={expanded === run.run_id ? faChevronDown : faChevronRight}
                          />
                        </button>
                      </td>
                    )}
                    {columns.map((column) =>
                      column === openColumn ? (
                        <td key={column} className="run-table__open-cell">
                          <button
                            type="button"
                            className="run-table__open"
                            onClick={() => openRun(run)}
                          >
                            {renderCell(run, column)}
                          </button>
                        </td>
                      ) : (
                        <td
                          key={column}
                          className={clsx(COLUMNS[column].align === "right" && "is-right")}
                        >
                          {renderCell(run, column)}
                        </td>
                      )
                    )}
                    {onDeleteRun && (
                      <td className="run-table__menu">
                        <RowMenu
                          ariaLabel={`Actions for run ${run.run_id}`}
                          items={[
                            {
                              danger: true,
                              label: "Delete",
                              onSelect: () => onDeleteRun(run),
                            },
                          ]}
                        />
                      </td>
                    )}
                  </tr>
                  {renderExpanded && expanded === run.run_id && (
                    <tr className="run-table__details">
                      <td colSpan={columns.length + extraColumns}>{renderExpanded(run)}</td>
                    </tr>
                  )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {paginate && !loading && rows.length > 0 && (
        <Pagination
          currentPage={page}
          setCurrentPage={setPage}
          totalPages={totalPages}
          itemsPerPage={perPage}
          setItemsPerPage={(items) => {
            setPerPage(items);
            setPage(1);
          }}
        />
      )}
    </div>
  );
};

export default RunTable;
