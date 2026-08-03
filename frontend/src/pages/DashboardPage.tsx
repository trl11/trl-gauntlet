import { faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useEffect, useState } from "react";
import { Link } from "react-router";

import {
  getSettings,
  getSystemData,
  getSystemInfo,
  getVersion,
  listInstruments,
  listRuns,
  listSuites,
  listUnits,
} from "@api/client";
import type { RunRow, Unit } from "@api/types";
import ActiveRun from "@components/ActiveRun";
import DefinitionRows, { type DefinitionRow } from "@components/DefinitionRows";
import EmptyState from "@components/EmptyState";
import HostHealth from "@components/HostHealth";
import OutcomeChart, { type OutcomeBucket } from "@components/OutcomeChart";
import PageHeader from "@components/PageHeader";
import Panel from "@components/Panel";
import RunTable from "@components/RunTable";
import StatusPill from "@components/StatusPill";
import { formatBytes, formatTimestamp, toDate } from "../utils/format";
import { isLive } from "../utils/run_status";

import "./DashboardPage.scss";

const DAY_MS = 86_400_000;
/** How many host samples the sparklines keep. */
const MAX_SAMPLES = 40;
/** How many serials the units card lists. */
const RECENT_UNITS = 10;
/** How many runs the recent runs table lists. */
const RECENT_RUNS = 10;

/** One reading of the figures the sparklines plot. */
interface HostSample {
  cpu: number;
  memory: number;
}

function countOutcomes(runs: RunRow[], label: string, since: number): OutcomeBucket {
  const bucket: OutcomeBucket = { failed: 0, label, other: 0, passed: 0 };
  for (const run of runs) {
    const ended = toDate(run.ended_at)?.getTime() ?? null;
    if (ended === null || ended < since) continue;
    if (run.status === "passed") bucket.passed += 1;
    else if (run.status === "failed") bucket.failed += 1;
    else if (!isLive(run.status)) bucket.other += 1;
  }
  return bucket;
}

/** The most recent run that names a unit, which is the unit on the bench. */
function lastUnitRun(runs: RunRow[]): RunRow | null {
  return runs.find((run) => run.unit_serial) ?? null;
}

/** The units seen most recently, newest first. */
function recentUnits(units: Unit[]): Unit[] {
  const ordered = [...units].sort((a, b) =>
    String(b.last_seen ?? "").localeCompare(String(a.last_seen ?? ""))
  );
  return ordered.slice(0, RECENT_UNITS);
}

/**
 * The first few state values an instrument reports, as `key value` pairs.
 * Objects and arrays are skipped; the panel on /instruments renders those.
 */
function stateSummary(state: Record<string, unknown>): string {
  return Object.entries(state)
    .filter(([, value]) => value === null || typeof value !== "object")
    .slice(0, 3)
    .map(([key, value]) => `${key} ${String(value)}`)
    .join(" · ");
}

/** What the bench is doing right now: live runs, host health, recent history. */
export const DashboardPage: React.FC = () => {
  const [now, setNow] = useState(() => Date.now());
  const [samples, setSamples] = useState<HostSample[]>([]);

  const runs = useQuery({
    queryKey: ["runs", "dashboard"],
    queryFn: () => listRuns({ limit: 200 }),
    refetchInterval: 5000,
  });
  const system = useQuery({
    queryKey: ["system", "data"],
    queryFn: getSystemData,
    refetchInterval: 3000,
  });
  const instruments = useQuery({
    queryKey: ["instruments"],
    queryFn: listInstruments,
    refetchInterval: 10_000,
  });
  const suites = useQuery({ queryKey: ["suites"], queryFn: listSuites });
  const units = useQuery({ queryKey: ["units"], queryFn: listUnits, refetchInterval: 10_000 });
  const info = useQuery({ queryKey: ["system-info"], queryFn: getSystemInfo });
  const settings = useQuery({ queryKey: ["settings"], queryFn: getSettings });
  const version = useQuery({ queryKey: ["version"], queryFn: getVersion });

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  useEffect(() => {
    const data = system.data;
    if (!data) return;
    const sample = { cpu: data.cpu_percent ?? 0, memory: data.memory.percent ?? 0 };
    setSamples((previous) => [...previous, sample].slice(-MAX_SAMPLES));
  }, [system.data, system.dataUpdatedAt]);

  const allRuns = runs.data?.runs ?? [];
  const active = allRuns.filter((run) => isLive(run.status));
  const onBench = lastUnitRun(allRuns);
  const unitRows = recentUnits(units.data?.units ?? []);
  const buckets = [
    countOutcomes(allRuns, "Last 24h", now - DAY_MS),
    countOutcomes(allRuns, "Last 7d", now - 7 * DAY_MS),
  ];
  const discoveryErrors = suites.data?.errors ?? [];
  const disks = system.data?.disks ?? [];
  const fullest = [...disks].sort((a, b) => b.percent - a.percent)[0];

  const environment: DefinitionRow[] = [
    { label: "app version", value: version.data?.gauntlet ?? "-" },
    { label: "contract", value: version.data?.contract_version ?? "-" },
    { label: "python", value: version.data?.python ?? "-" },
    { label: "platform", value: version.data?.platform ?? info.data?.os ?? "-" },
    {
      label: "memory",
      value: `${formatBytes(system.data?.memory.used)} / ${formatBytes(info.data?.memory_total_bytes)}`,
    },
    { label: "free disk", value: fullest ? formatBytes(fullest.free) : "-" },
    { label: "runs dir", value: settings.data?.runs_dir ?? "-" },
  ];

  return (
    <div className="dashboard-page">
      <PageHeader title="Dashboard" subtitle="What the bench is doing right now" />

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

      <Panel title={active.length > 0 ? "Active run" : "Unit under test"}>
        {runs.isPending ? (
          <Spinner />
        ) : active.length > 0 ? (
          <div className="dashboard-page__actives">
            {active.map((run) => (
              <ActiveRun key={run.run_id} now={now} run={run} />
            ))}
          </div>
        ) : onBench === null ? (
          <EmptyState
            title="Nothing running"
            message="Start a suite from Tests and it will appear here."
            action={<Link to="/tests">Run a test</Link>}
          />
        ) : (
          <DefinitionRows
            rows={[
              { label: "serial", value: onBench.unit_serial },
              { label: "last suite", value: onBench.suite },
              { label: "verdict", value: <StatusPill status={onBench.status} /> },
            ]}
          />
        )}
      </Panel>

      <Panel
        title="Recent runs"
        action={
          <Link className="panel__action" to="/history">
            all runs →
          </Link>
        }
      >
        <RunTable
          runs={allRuns.slice(0, RECENT_RUNS)}
          loading={runs.isPending}
          filterable={false}
          pageSize={0}
          columns={["started_at", "duration_s", "unit_serial", "suite", "status"]}
          emptyMessage="No runs recorded yet."
        />
      </Panel>

      <Panel
        className="dashboard-page__units-panel"
        title="Units"
        action={
          <Link className="panel__action" to="/units">
            all units →
          </Link>
        }
      >
        {units.isPending ? (
          <Spinner />
        ) : unitRows.length === 0 ? (
          <p className="dashboard-page__quiet">No unit has been on the bench yet.</p>
        ) : (
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
                    <Link to={`/units/${encodeURIComponent(unit.serial)}`}>{unit.serial}</Link>
                  </td>
                  <td className="is-right">{unit.run_count}</td>
                  <td className={clsx("is-right", unit.passed > 0 && "is-passed")}>
                    {unit.passed}
                  </td>
                  <td className={clsx("is-right", unit.failed > 0 && "is-failed")}>
                    {unit.failed}
                  </td>
                  <td>{formatTimestamp(unit.last_seen)}</td>
                  <td>
                    {unit.last_run ? (
                      <StatusPill status={unit.last_run.status} />
                    ) : (
                      <span className="dashboard-page__quiet">-</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>

      <Panel title="Outcomes">
        <OutcomeChart buckets={buckets} />
      </Panel>

      <Panel
        title="Instruments"
        action={
          <Link className="panel__action" to="/instruments">
            manage →
          </Link>
        }
      >
        {instruments.isPending ? (
          <Spinner />
        ) : (instruments.data?.instruments ?? []).length === 0 ? (
          <EmptyState title="No instruments" message="Gauntlet is holding no instruments." />
        ) : (
          <div className="dashboard-page__instruments">
            {(instruments.data?.instruments ?? []).map((instrument) => (
              <Link
                className="dashboard-page__instrument"
                key={instrument.name}
                to="/instruments"
                aria-label={`${instrument.name}, ${instrument.available ? "available" : "unavailable"}`}
              >
                <span
                  className={clsx(
                    "dashboard-page__dot",
                    instrument.available && "dashboard-page__dot--on"
                  )}
                  aria-hidden="true"
                />
                <span className="dashboard-page__instrument-name">{instrument.name}</span>
                <span className="dashboard-page__instrument-state">
                  {stateSummary(instrument.state) || instrument.kind}
                </span>
              </Link>
            ))}
          </div>
        )}
      </Panel>

      <Panel title="Host health">
        {system.isPending ? (
          <Spinner />
        ) : system.isError ? (
          <p className="dashboard-page__error">Host telemetry is unavailable.</p>
        ) : (
          <HostHealth
            cpuHistory={samples.map((sample) => sample.cpu)}
            data={system.data}
            memoryHistory={samples.map((sample) => sample.memory)}
          />
        )}
      </Panel>

      <div className="dashboard-page__environment">
        <Panel
          title="Environment"
          action={
            <Link className="panel__action" to="/settings">
              settings →
            </Link>
          }
        >
          <DefinitionRows rows={environment} />
        </Panel>
      </div>
    </div>
  );
};

export default DashboardPage;
