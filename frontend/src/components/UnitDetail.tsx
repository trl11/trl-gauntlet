import { faArrowLeft } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DataField, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useState } from "react";
import { Link, useNavigate } from "react-router";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import {
  addUnitNote,
  deleteUnitNote,
  getUnit,
  getUnitHistory,
  listUnitNotes,
  renameUnit,
} from "@api/client";
import type { RunRow } from "@api/types";
import EmptyState from "@components/EmptyState";
import NotesPanel from "@components/NotesPanel";
import PageHeader from "@components/PageHeader";
import RenameDialog from "@components/RenameDialog";
import RunTable from "@components/RunTable";
import { formatPercent, formatTimestamp } from "../utils/format";
import { health } from "../utils/health";

import "./UnitDetail.scss";

/** Props for {@link UnitDetail}. */
export interface UnitDetailProps {
  /** Serial of the unit to show. */
  serial: string;
}

/** Cumulative pass rate after each run, oldest first. */
function passRateSeries(runs: RunRow[]): Array<{ rate: number; run: string; started: string }> {
  const ordered = [...runs].sort((a, b) => a.started_at.localeCompare(b.started_at));
  let passed = 0;
  return ordered.map((run, index) => {
    if (run.status === "passed") passed += 1;
    return {
      rate: (passed / (index + 1)) * 100,
      run: run.run_id,
      started: formatTimestamp(run.started_at, { second: undefined }),
    };
  });
}

/** One unit: its counters, its pass rate over time, its runs, and its notes. */
export const UnitDetail: React.FC<UnitDetailProps> = ({ serial }) => {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [renaming, setRenaming] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);

  const unit = useQuery({ queryKey: ["unit", serial], queryFn: () => getUnit(serial) });
  const history = useQuery({
    queryKey: ["unit-history", serial],
    queryFn: () => getUnitHistory(serial),
  });
  const notes = useQuery({
    queryKey: ["unit-notes", serial],
    queryFn: () => listUnitNotes(serial),
  });

  const refreshNotes = () => {
    client.invalidateQueries({ queryKey: ["unit-notes", serial] });
    client.invalidateQueries({ queryKey: ["unit", serial] });
    client.invalidateQueries({ queryKey: ["units"] });
  };

  const addNote = useMutation({
    mutationFn: (input: { author: string | null; body: string }) =>
      addUnitNote(serial, input.body, input.author),
    onSuccess: refreshNotes,
  });

  const removeNote = useMutation({
    mutationFn: (noteId: number) => deleteUnitNote(serial, noteId),
    onSuccess: refreshNotes,
  });

  const rename = useMutation({
    mutationFn: (next: string) => renameUnit(serial, next),
    onSuccess: (renamed) => {
      client.invalidateQueries({ queryKey: ["units"] });
      navigate(`/units/${encodeURIComponent(renamed.serial)}`, { replace: true });
    },
    onError: (error) => setRenameError(error.message),
  });

  const runs = history.data?.runs ?? [];
  const series = passRateSeries(runs);
  const rate =
    unit.data && unit.data.run_count > 0 ? (unit.data.passed / unit.data.run_count) * 100 : null;

  return (
    <div className="unit-detail">
      <PageHeader
        title={<span className="unit-detail__serial">{serial}</span>}
        actions={
          <Button
            size="small"
            onClick={() => {
              setRenameError(null);
              setRenaming(true);
            }}
          >
            Rename
          </Button>
        }
      >
        <Link to="/units" className="unit-detail__back">
          <FontAwesomeIcon icon={faArrowLeft} aria-hidden="true" />
          All units
        </Link>
      </PageHeader>

      {renaming && (
        <RenameDialog
          busy={rename.isPending}
          error={renameError}
          serial={serial}
          onCancel={() => setRenaming(false)}
          onRename={(next) => rename.mutate(next)}
        />
      )}

      {unit.isPending && <Spinner />}

      {unit.isError && <EmptyState title="Unknown unit" message={unit.error.message} />}

      {unit.isSuccess && (
        <div className="unit-detail__facts">
          <DataField label="Runs" value={String(unit.data.run_count)} />
          <DataField label="Passed" value={String(unit.data.passed)} color="green" />
          <DataField label="Failed" value={String(unit.data.failed)} color="red" />
          <DataField label="Pass rate" value={formatPercent(rate, 0)} />
          <DataField label="First seen" value={formatTimestamp(unit.data.first_seen)} />
          <DataField label="Last seen" value={formatTimestamp(unit.data.last_seen)} />
        </div>
      )}

      <section className="unit-detail__section" aria-label="Pass rate over time">
        <h2 className="unit-detail__title">Pass rate over time</h2>
        {series.length === 0 ? (
          <p className="unit-detail__quiet">No runs to chart yet.</p>
        ) : (
          <div className={clsx("unit-detail__chart", health(rate ?? series.at(-1)?.rate ?? 0))}>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={series} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
                <CartesianGrid vertical={false} />
                <XAxis dataKey="started" minTickGap={40} />
                <YAxis domain={[0, 100]} width={48} unit="%" />
                <ChartTooltip formatter={(value: number) => formatPercent(value, 1)} />
                <Line
                  type="monotone"
                  dataKey="rate"
                  name="Cumulative pass rate"
                  stroke="currentColor"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                />
              </LineChart>
            </ResponsiveContainer>
          </div>
        )}
      </section>

      <section className="unit-detail__section" aria-label="Run history">
        <h2 className="unit-detail__title">Run history</h2>
        <RunTable
          runs={runs}
          loading={history.isPending}
          columns={["status", "suite", "run_id", "profile", "started_at", "duration_s"]}
          emptyMessage="No runs have named this unit."
        />
      </section>

      <NotesPanel
        className="unit-detail__notes"
        notes={notes.data?.notes ?? []}
        busy={notes.isPending || addNote.isPending || removeNote.isPending}
        onAdd={(body, author) => addNote.mutateAsync({ author, body })}
        onDelete={(noteId) => removeNote.mutateAsync(noteId)}
      />
    </div>
  );
};

export default UnitDetail;
