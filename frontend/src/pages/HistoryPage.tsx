import { useQuery } from "@tanstack/react-query";
import { Button, FilterMenu, Pagination } from "@trl11/components/ui";
import { useState } from "react";
import { useSearchParams } from "react-router";

import { listRuns, listSuites, listUnits } from "@api/client";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";
import RunDetails from "@components/RunDetails";
import RunTable, { type SortDirection } from "@components/RunTable";
import type { RunTableColumn } from "@components/run_columns";
import { downloadCsv, toCsv } from "../utils/run_csv";
import { LIVE_STATUSES } from "../utils/run_status";

import "./HistoryPage.scss";

/** The filter values, in the shape the ui-kit `FilterMenu` holds them. */
type Filters = React.ComponentProps<typeof FilterMenu>["filterState"];

/** Columns the table renders, left to right. */
const COLUMNS: RunTableColumn[] = [
  "started_at",
  "duration_s",
  "suite",
  "profile",
  "unit_serial",
  "status",
];

/** Statuses a finished or in-flight run can carry. `live` stands for all four in-flight ones. */
const STATUS_OPTIONS = [
  { value: "all", label: "Any status" },
  { value: "passed", label: "Passed" },
  { value: "failed", label: "Failed" },
  { value: "aborted", label: "Aborted" },
  { value: "error", label: "Error" },
  { value: "live", label: "In flight" },
];

/** Which statuses one filter value asks the API for. */
function statusFilter(value: string): string[] {
  if (value === "all") return [];
  if (value === "live") return LIVE_STATUSES;
  return [value];
}

/** Every recorded run, filtered and paged by the server, selectable and exportable. */
export const HistoryPage: React.FC = () => {
  const [params, setParams] = useSearchParams();
  const [selected, setSelected] = useState<string[]>([]);

  const page = Math.max(1, Number(params.get("page") ?? 1) || 1);
  const size = Math.max(1, Number(params.get("size") ?? 20) || 20);
  const sort = (params.get("sort") ?? "started_at") as RunTableColumn;
  const direction: SortDirection = params.get("dir") === "asc" ? "asc" : "desc";

  const filters: Filters = {
    after: params.get("after") || "all",
    before: params.get("before") || "all",
    status: params.get("status") || "all",
    suite: params.get("suite") || "all",
    unit: params.get("unit") || "all",
  };

  const write = (changes: Record<string, string>) => {
    setParams((current) => {
      const next = new URLSearchParams(current);
      for (const [key, value] of Object.entries(changes)) {
        if (!value || value === "all") next.delete(key);
        else next.set(key, value);
      }
      return next;
    });
  };

  const setFilters: React.Dispatch<React.SetStateAction<Filters>> = (value) => {
    const next = typeof value === "function" ? value(filters) : value;
    const changes: Record<string, string> = { page: "" };
    for (const [key, entry] of Object.entries(next)) changes[key] = String(entry ?? "");
    write(changes);
  };

  const suites = useQuery({ queryKey: ["suites"], queryFn: listSuites });
  const units = useQuery({ queryKey: ["units"], queryFn: listUnits });
  const query = {
    after: filters.after === "all" ? null : String(filters.after),
    before: filters.before === "all" ? null : String(filters.before),
    direction,
    limit: size,
    offset: (page - 1) * size,
    sort,
    status: statusFilter(String(filters.status)),
    suite: filters.suite === "all" ? null : String(filters.suite),
    unit_serial: filters.unit === "all" ? null : String(filters.unit),
  };
  const runs = useQuery({
    queryKey: ["runs", "history", query],
    queryFn: () => listRuns(query),
  });

  const rows = runs.data?.runs ?? [];
  const total = runs.data?.total ?? 0;
  const selectedRows = rows.filter((run) => selected.includes(run.run_id));
  const totalPages = Math.max(1, Math.ceil(total / size));

  return (
    <div className="history-page">
      <PageHeader
        title="History"
        actions={
          <FilterMenu
            filterState={filters}
            setFilterState={setFilters}
            filters={[
              {
                id: "suite",
                options: [
                  { value: "all", label: "Any suite" },
                  ...(suites.data?.suites ?? []).map((suite) => ({
                    value: suite.key,
                    label: suite.title || suite.key,
                  })),
                ],
              },
              { id: "status", options: STATUS_OPTIONS },
              {
                id: "unit",
                options: [
                  { value: "all", label: "Any unit" },
                  ...(units.data?.units ?? []).map((unit) => ({
                    value: unit.serial,
                    label: unit.serial,
                  })),
                ],
              },
              { id: "after", select: false, type: "date", label: "Started on or after" },
              { id: "before", select: false, type: "date", label: "Started on or before" },
            ]}
          />
        }
      />

      <div className="history-page__bar">
        <span className="history-page__count">
          {`${rows.length} of ${total} · page ${page} of ${totalPages}`}
          {runs.isFetching ? " · refreshing" : ""}
        </span>
        <span className="history-page__bulk">
          {`${selected.length} selected`}
          <Button
            size="small"
            disabled={selectedRows.length === 0}
            onClick={() => downloadCsv(toCsv(selectedRows))}
          >
            Export CSV
          </Button>
          <Button size="small" disabled={selected.length === 0} onClick={() => setSelected([])}>
            Clear
          </Button>
        </span>
      </div>

      {runs.isError ? (
        <p className="history-page__error">{(runs.error as Error).message}</p>
      ) : (
        <Panel title="Runs">
          <RunTable
            columns={COLUMNS}
            direction={direction}
            emptyMessage="Nothing matches these filters."
            filterable={false}
            loading={runs.isPending}
            onSelectionChange={setSelected}
            onSort={(column, next) => write({ dir: next, sort: column })}
            pageSize={0}
            renderExpanded={(run) => <RunDetails run={run} />}
            runs={rows}
            selectedIds={selected}
            sort={sort}
          />
        </Panel>
      )}

      <Pagination
        currentPage={page}
        setCurrentPage={(next) => write({ page: String(next) })}
        totalPages={totalPages}
        itemsPerPage={size}
        setItemsPerPage={(items) => write({ page: "1", size: String(items) })}
      />
    </div>
  );
};

export default HistoryPage;
