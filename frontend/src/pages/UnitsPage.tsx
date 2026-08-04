import {
  faCircleCheck,
  faCircleExclamation,
  faPen,
  faSort,
  faSortDown,
  faSortUp,
  faTrash,
  faTriangleExclamation,
} from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Confirm, Input, TableSkeleton } from "@trl11/components/ui";
import clsx from "clsx";
import { useId, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router";

import { deleteUnit, listUnits, renameUnit } from "@api/client";
import type { Unit } from "@api/types";
import EmptyState from "@components/EmptyState";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";
import RenameDialog from "@components/RenameDialog";
import StatusPill from "@components/StatusPill";
import UnitDetail from "@components/UnitDetail";
import { formatPercent, formatTimestamp } from "../utils/format";
import { health } from "../utils/health";

import "./UnitsPage.scss";

type SortKey =
  "failed" | "first_seen" | "last_seen" | "pass_rate" | "passed" | "run_count" | "serial";

const COLUMNS: Array<{ align?: "right"; header: string; key: SortKey }> = [
  { header: "Serial", key: "serial" },
  { align: "right", header: "Pass rate", key: "pass_rate" },
  { align: "right", header: "Runs", key: "run_count" },
  { align: "right", header: "Passed", key: "passed" },
  { align: "right", header: "Failed", key: "failed" },
  { header: "First seen", key: "first_seen" },
  { header: "Last seen", key: "last_seen" },
];

/** Share of a unit's runs that passed, as a percentage. */
function passRate(unit: Unit): number | null {
  return unit.run_count > 0 ? (unit.passed / unit.run_count) * 100 : null;
}

function compare(a: Unit, b: Unit, key: SortKey): number {
  if (key === "pass_rate") return (passRate(a) ?? -1) - (passRate(b) ?? -1);
  if (key === "serial") return a.serial.localeCompare(b.serial);
  if (key === "first_seen" || key === "last_seen") {
    return String(a[key] ?? "").localeCompare(String(b[key] ?? ""));
  }
  return a[key] - b[key];
}

/** The icon paired with a health tier, so it doesn't read by colour alone. */
const HEALTH_ICON = {
  "is-good": faCircleCheck,
  "is-fair": faTriangleExclamation,
  "is-poor": faCircleExclamation,
};

/** The pass-rate bar, coloured by how healthy the unit looks and iconed to match. */
const PassRate: React.FC<{ rate: number | null }> = ({ rate }) => {
  if (rate === null) return <span className="units-page__quiet">-</span>;
  const tier = health(rate);
  return (
    <span className="units-page__rate">
      <span className="units-page__rate-track">
        <span className={clsx("units-page__rate-fill", tier)} style={{ width: `${rate}%` }} />
      </span>
      <FontAwesomeIcon className={clsx("units-page__rate-icon", tier)} icon={HEALTH_ICON[tier]} />
      <span className="units-page__rate-text">{formatPercent(rate, 0)}</span>
    </span>
  );
};

/** Every unit, sortable and searchable, with rename and delete. */
const UnitsList: React.FC = () => {
  const client = useQueryClient();
  const navigate = useNavigate();
  const fieldId = useId();

  const [search, setSearch] = useState("");
  const [sortKey, setSortKey] = useState<SortKey>("last_seen");
  const [ascending, setAscending] = useState(false);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [deleting, setDeleting] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);

  const units = useQuery({ queryKey: ["units"], queryFn: listUnits });

  const settled = () => {
    setRenaming(null);
    setDeleting(null);
    setFailure(null);
    client.invalidateQueries({ queryKey: ["units"] });
    client.invalidateQueries({ queryKey: ["runs"] });
  };

  const rename = useMutation({
    mutationFn: (input: { from: string; to: string }) => renameUnit(input.from, input.to),
    onSuccess: settled,
    onError: (error) => setFailure(error.message),
  });

  const remove = useMutation({
    mutationFn: (target: string) => deleteUnit(target),
    onSuccess: settled,
    onError: (error) => setFailure(error.message),
  });

  const rows = useMemo(() => {
    const needle = search.trim().toLowerCase();
    const filtered = (units.data?.units ?? []).filter((unit) =>
      unit.serial.toLowerCase().includes(needle)
    );
    const sign = ascending ? 1 : -1;
    return [...filtered].sort((a, b) => sign * compare(a, b, sortKey));
  }, [units.data, search, sortKey, ascending]);

  const sortBy = (key: SortKey) => {
    if (key === sortKey) setAscending((current) => !current);
    else {
      setSortKey(key);
      setAscending(key === "serial");
    }
  };

  const open = (target: string) => navigate(`/units/${encodeURIComponent(target)}`);

  return (
    <div className="units-page">
      <PageHeader
        title="Units"
        actions={
          <Input
            id={`${fieldId}-search`}
            type="search"
            placeholder="Filter by serial"
            aria-label="Filter units"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
          />
        }
      />

      {failure && renaming === null && (
        <p className="units-page__error" role="alert">
          {failure}
        </p>
      )}

      {units.isPending && <TableSkeleton rows={6} />}

      {units.isError && (
        <EmptyState title="Could not read the units" message={units.error.message} />
      )}

      {units.isSuccess && rows.length === 0 && (
        <EmptyState
          title="No units yet"
          message="Start a run with a unit serial and it will appear here."
        />
      )}

      {rows.length > 0 && (
        <Panel className="units-page__panel" title="Inventory">
          <table className="units-page__table">
            <thead>
              <tr>
                {COLUMNS.map((column) => (
                  <th
                    key={column.key}
                    scope="col"
                    className={clsx(column.align === "right" && "is-right")}
                    aria-sort={
                      column.key === sortKey ? (ascending ? "ascending" : "descending") : "none"
                    }
                  >
                    <button
                      type="button"
                      className="units-page__sort"
                      onClick={() => sortBy(column.key)}
                    >
                      {column.header}
                      <FontAwesomeIcon
                        icon={column.key !== sortKey ? faSort : ascending ? faSortUp : faSortDown}
                        aria-hidden="true"
                      />
                    </button>
                  </th>
                ))}
                <th scope="col">Last run</th>
                <th scope="col" aria-label="Actions" />
              </tr>
            </thead>
            <tbody>
              {rows.map((unit) => (
                <tr key={unit.serial}>
                  <td className="units-page__open-cell">
                    <button
                      type="button"
                      className="units-page__open units-page__serial"
                      onClick={() => open(unit.serial)}
                    >
                      {unit.serial}
                    </button>
                  </td>
                  <td className="is-right">
                    <PassRate rate={passRate(unit)} />
                  </td>
                  <td className="is-right">{unit.run_count}</td>
                  <td className="is-right">{unit.passed}</td>
                  <td className="is-right">{unit.failed}</td>
                  <td>{formatTimestamp(unit.first_seen)}</td>
                  <td>{formatTimestamp(unit.last_seen)}</td>
                  <td>
                    {unit.last_run ? (
                      <StatusPill status={unit.last_run.status} />
                    ) : (
                      <span className="units-page__quiet">-</span>
                    )}
                  </td>
                  <td className="units-page__actions">
                    <Button
                      size="small"
                      square
                      color="transparent"
                      aria-label={`Rename unit ${unit.serial}`}
                      onClick={() => {
                        setFailure(null);
                        setRenaming(unit.serial);
                      }}
                    >
                      <FontAwesomeIcon icon={faPen} />
                    </Button>
                    <Button
                      size="small"
                      square
                      color="red"
                      aria-label={`Delete unit ${unit.serial}`}
                      onClick={() => {
                        setFailure(null);
                        setDeleting(unit.serial);
                      }}
                    >
                      <FontAwesomeIcon icon={faTrash} />
                    </Button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Panel>
      )}

      {renaming !== null && (
        <RenameDialog
          busy={rename.isPending}
          error={failure}
          serial={renaming}
          onCancel={() => setRenaming(null)}
          onRename={(next) => rename.mutate({ from: renaming, to: next })}
        />
      )}

      {deleting !== null && (
        <Confirm onConfirm={() => remove.mutate(deleting)} onDismiss={() => setDeleting(null)}>
          {`Forget unit ${deleting}? Its runs stay in history; its notes and counters are removed.`}
        </Confirm>
      )}
    </div>
  );
};

/** The unit database: the list, or one unit when the route names it. */
export const UnitsPage: React.FC = () => {
  const { serial } = useParams<{ serial: string }>();
  return serial ? <UnitDetail serial={serial} /> : <UnitsList />;
};

export default UnitsPage;
