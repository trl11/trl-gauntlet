import { faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import { listInstruments, listRuns, listSuites, listUnits } from "@api/client";
import type { Unit } from "@api/types";
import ActiveRun from "@components/ActiveRun";
import InstrumentTile from "@components/InstrumentTile";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";
import RunTable from "@components/RunTable";
import StatusPill from "@components/StatusPill";
import { formatDuration, formatRelativeTime, formatTimestamp } from "../utils/format";
import { isLive } from "../utils/run_status";

import "./DashboardPage.scss";

/** How many serials the units card lists. */
const RECENT_UNITS = 10;
/** How many runs the recent runs table lists. */
const RECENT_RUNS = 10;

/** Props for {@link SectionHead}. */
interface SectionHeadProps {
  /** A link aligned right in the heading row. */
  action?: React.ReactNode;
  title: string;
}

/** The label above a block of the dashboard that is not drawn as a card. */
const SectionHead: React.FC<SectionHeadProps> = ({ action, title }) => (
  <div className="dashboard-page__section-head">
    <h2 className="dashboard-page__section-title">{title}</h2>
    {action}
  </div>
);

/**
 * What the first card calls itself.
 *
 * With something in flight the card is that run. With nothing running it is
 * the run that finished last, whatever it was and whether or not it named a
 * unit: what the bench did most recently is the thing an operator walking up
 * to it wants to see.
 */
function benchTitle(running: boolean, ran: boolean): string {
  if (running) return "Active run";
  return ran ? "Last test run" : "Nothing has run yet";
}

/** The units seen most recently, newest first. */
function recentUnits(units: Unit[]): Unit[] {
  const ordered = [...units].sort((a, b) =>
    String(b.last_seen ?? "").localeCompare(String(a.last_seen ?? ""))
  );
  return ordered.slice(0, RECENT_UNITS);
}

/** What the bench is doing right now: live runs, instruments, recent history. */
export const DashboardPage: React.FC = () => {
  const [now, setNow] = useState(() => Date.now());

  const runs = useQuery({
    queryKey: ["runs", "dashboard"],
    queryFn: () => listRuns({ limit: 200 }),
    refetchInterval: 5000,
  });
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
    refetchInterval: 10_000,
  });
  const suites = useQuery({ queryKey: ["suites"], queryFn: listSuites });
  const units = useQuery({ queryKey: ["units"], queryFn: listUnits, refetchInterval: 10_000 });

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const allRuns = runs.data?.runs ?? [];
  const active = allRuns.filter((run) => isLive(run.status));
  // The history is newest first, so the run that finished last is the first
  // one that is not still going.
  const lastRun = allRuns.find((run) => !isLive(run.status)) ?? null;
  const instrumentRows = instruments.data?.instruments ?? [];
  const unitRows = recentUnits(units.data?.units ?? []);
  const discoveryErrors = suites.data?.errors ?? [];

  // The record the units list already holds for the unit that run named, so
  // the card counts its history without asking for anything of its own. A run
  // that named no unit has none, and the card says so.
  const benchRecord = lastRun?.unit_serial
    ? units.data?.units.find((unit) => unit.serial === lastRun.unit_serial)
    : undefined;

  return (
    <div className="dashboard-page">
      <PageHeader title="Dashboard" />

      {discoveryErrors.length > 0 && (
        <div className="dashboard-page__banner" role="alert">
          <FontAwesomeIcon icon={faTriangleExclamation} aria-hidden="true" />
          <div>
            <p>{`Suite discovery reported ${discoveryErrors.length} problem(s)`}</p>
            <ul>
              {discoveryErrors.map((message) => (
                <li key={message}>{message}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <Panel
        title={benchTitle(active.length > 0, lastRun !== null)}
        action={
          // A finished run says when it ran, so hours-old history is not read
          // as what the bench is doing right now.
          active.length === 0 &&
          lastRun !== null && (
            <span className="dashboard-page__tested">
              {`ran ${formatTimestamp(lastRun.started_at, { second: undefined })} · ${formatRelativeTime(lastRun.started_at, new Date(now))}`}
            </span>
          )
        }
      >
        <div className="dashboard-page__unit-panel">
          {runs.isPending ? (
            <Spinner />
          ) : active.length > 0 ? (
            <div className="dashboard-page__actives">
              {active.map((run) => (
                <ActiveRun key={run.run_id} now={now} run={run} />
              ))}
            </div>
          ) : lastRun === null ? (
            <div className="dashboard-page__idle">
              <p>Nothing running</p>
              <Link to="/tests">Run a test</Link>
            </div>
          ) : (
            <Link
              aria-label={`Open the last run, ${lastRun.suite}`}
              className="dashboard-page__unit"
              to={`/runs/${encodeURIComponent(lastRun.run_id)}`}
            >
              <div className="dashboard-page__unit-head">
                <span className="dashboard-page__unit-serial">{lastRun.suite}</span>
                <p className="dashboard-page__unit-suite">
                  {lastRun.unit_serial ? `unit ${lastRun.unit_serial}` : "no unit named"}
                </p>
              </div>

              <dl className="dashboard-page__unit-facts">
                <div>
                  <dt>profile</dt>
                  <dd>{lastRun.profile ?? "-"}</dd>
                </div>
                <div>
                  <dt>took</dt>
                  <dd>{lastRun.duration_s == null ? "-" : formatDuration(lastRun.duration_s)}</dd>
                </div>
                <div>
                  <dt>unit runs</dt>
                  <dd
                    className={clsx(
                      benchRecord && benchRecord.failed > 0 && "is-failed",
                      benchRecord &&
                        benchRecord.failed === 0 &&
                        benchRecord.passed > 0 &&
                        "is-passed"
                    )}
                  >
                    {benchRecord?.run_count ?? "-"}
                  </dd>
                </div>
              </dl>

              <StatusPill status={lastRun.status} />
            </Link>
          )}
        </div>
      </Panel>

      <section className="dashboard-page__section">
        <SectionHead
          title="Recent runs"
          action={
            <Link className="dashboard-page__section-action" to="/history">
              {/* The total the server counted, not the page of it fetched here. */}
              {runs.data ? `all runs (${runs.data.total}) →` : "all runs →"}
            </Link>
          }
        />
        <RunTable
          runs={allRuns.slice(0, RECENT_RUNS)}
          loading={runs.isPending}
          filterable={false}
          pageSize={0}
          columns={[
            "started_at",
            "duration_s",
            "unit_serial",
            "suite",
            "profile",
            "note_count",
            "status",
          ]}
          emptyMessage="No runs recorded yet."
        />
      </section>

      <section className="dashboard-page__section">
        <SectionHead
          title="Units"
          action={
            <Link className="dashboard-page__section-action" to="/units">
              {units.data ? `all units (${units.data.units.length}) →` : "all units →"}
            </Link>
          }
        />
        {units.isPending ? (
          <Spinner />
        ) : unitRows.length === 0 ? (
          <p className="dashboard-page__quiet">No unit has been on the bench yet.</p>
        ) : (
          <div className="dashboard-page__units-scroll">
            <table className="dashboard-page__units">
              <thead>
                <tr>
                  <th scope="col">Serial</th>
                  <th scope="col" className="is-right">
                    Runs
                  </th>
                  <th scope="col" className="is-right">
                    Passed
                  </th>
                  <th scope="col" className="is-right">
                    Failed
                  </th>
                  <th scope="col">Last tested</th>
                  <th scope="col">Last run</th>
                </tr>
              </thead>
              <tbody>
                {unitRows.map((unit) => (
                  <tr key={unit.serial}>
                    <td>
                      <Link
                        className="dashboard-page__serial"
                        to={`/units/${encodeURIComponent(unit.serial)}`}
                      >
                        {unit.serial}
                      </Link>
                    </td>
                    <td className="is-right">{unit.run_count}</td>
                    <td className={clsx("is-right", unit.passed > 0 && "is-passed")}>
                      {unit.passed}
                    </td>
                    <td className={clsx("is-right", unit.failed > 0 && "is-failed")}>
                      {unit.failed}
                    </td>
                    <td>{formatTimestamp(unit.last_seen)}</td>
                    <td>{unit.last_run ? <StatusPill status={unit.last_run.status} /> : "-"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      <section className="dashboard-page__section">
        <SectionHead title="Instruments" />
        <div className="dashboard-page__tiles">
          {instruments.isPending ? (
            <Spinner />
          ) : (
            instrumentRows.map((instrument) => (
              <InstrumentTile instrument={instrument} key={instrument.name} />
            ))
          )}
        </div>

        {!instruments.isPending && instrumentRows.length === 0 && (
          <p className="dashboard-page__quiet">No instrument is registered.</p>
        )}
      </section>
    </div>
  );
};

export default DashboardPage;
