import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Confirm, Spinner } from "@trl11/components/ui";
import clsx from "clsx";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";

import {
  abortRun,
  addRunNote,
  deleteRunNote,
  getArtifactText,
  getRun,
  getRunManifest,
  getRunMetrics,
  getRunVerdict,
  listArtifacts,
  listRunNotes,
  listSuites,
  stopRun,
} from "@api/client";
import ArtifactList from "@components/ArtifactList";
import DefinitionRows from "@components/DefinitionRows";
import EmptyState from "@components/EmptyState";
import IterationMap from "@components/IterationMap";
import IterationTable from "@components/IterationTable";
import LogStream from "@components/LogStream";
import MetricsChart from "@components/MetricsChart";
import NotesPanel from "@components/NotesPanel";
import PageHeader from "@components/PageHeader";
import VerdictBanner from "@components/VerdictBanner";
import VerdictSummary from "@components/VerdictSummary";
import useEventStream from "@hooks/useEventStream";
import { formatDuration, formatTimestamp } from "../utils/format";
import { elapsedSeconds, parseLog, replay, type AnomalyRow } from "../utils/run_history";
import { isLive } from "../utils/run_status";

import "./RunPage.scss";

/** How often the run row is refreshed while the run is in flight. */
const LIVE_POLL_MS = 2000;

const MAX_LOG_LINES = 50_000;
const MAX_METRIC_SAMPLES = 20_000;

const TABS = ["overview", "log", "metrics", "iterations", "artifacts", "notes"] as const;

type Tab = (typeof TABS)[number];

/** One run: its log, metrics, iterations, artifacts, verdict and notes. */
export const RunPage: React.FC = () => {
  const { runId = "" } = useParams<{ runId: string }>();
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<Tab>("overview");
  const [pending, setPending] = useState<"abort" | "stop" | null>(null);
  const [selected, setSelected] = useState<number | null>(null);

  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    refetchInterval: (query) => (isLive(query.state.data?.status) ? LIVE_POLL_MS : false),
  });
  const live = isLive(run.data?.status);
  const settled = run.data !== undefined && !live;

  // Cached alongside every other page that lists suites, so this rarely
  // triggers its own request. Only its manifest's default_metrics is used
  // here, to seed the Metrics/Iterations pickers before the operator picks
  // their own.
  const suites = useQuery({ queryKey: ["suites"], queryFn: listSuites });
  const defaultMetrics =
    suites.data?.suites.find((suite) => suite.key === run.data?.suite)?.default_metrics ?? [];

  const stream = useEventStream({
    runId,
    enabled: live,
    maxLogLines: MAX_LOG_LINES,
    maxMetricSamples: MAX_METRIC_SAMPLES,
  });

  const metrics = useQuery({
    queryKey: ["run-metrics", runId],
    queryFn: () => getRunMetrics(runId),
    enabled: settled,
    retry: false,
  });
  const verdict = useQuery({
    queryKey: ["run-verdict", runId],
    queryFn: () => getRunVerdict(runId),
    enabled: settled,
    retry: false,
  });
  // manifest.json is written when the suite process ends, so asking for it
  // during a run only produces a 404.
  const manifest = useQuery({
    queryKey: ["run-manifest", runId],
    queryFn: () => getRunManifest(runId),
    enabled: settled,
    retry: false,
  });
  const artifacts = useQuery({
    queryKey: ["artifacts", runId],
    queryFn: () => listArtifacts(runId),
    enabled: run.data !== undefined,
    refetchInterval: live ? 5000 : false,
  });

  const files = artifacts.data?.artifacts ?? [];
  const hasFile = (path: string) => files.some((file) => file.path === path);

  const logFile = useQuery({
    queryKey: ["artifact-text", runId, "test.log"],
    queryFn: () => getArtifactText(runId, "test.log"),
    enabled: settled && hasFile("test.log"),
    retry: false,
  });
  const summaryFile = useQuery({
    queryKey: ["artifact-text", runId, "summary.md"],
    queryFn: () => getArtifactText(runId, "summary.md"),
    enabled: settled && hasFile("summary.md"),
    retry: false,
  });

  const notes = useQuery({
    queryKey: ["run-notes", runId],
    queryFn: () => listRunNotes(runId),
    enabled: run.data !== undefined,
  });
  const refreshNotes = () => queryClient.invalidateQueries({ queryKey: ["run-notes", runId] });
  const addNote = useMutation({
    mutationFn: (note: { author: string | null; body: string }) =>
      addRunNote(runId, note.body, note.author),
    onSuccess: refreshNotes,
  });
  const removeNote = useMutation({
    mutationFn: (noteId: number) => deleteRunNote(runId, noteId),
    onSuccess: refreshNotes,
  });

  const control = useMutation({
    mutationFn: (action: "abort" | "stop") =>
      action === "stop" ? stopRun(runId) : abortRun(runId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["run", runId] }),
  });

  const replayed = useMemo(() => replay(metrics.data?.records ?? []), [metrics.data]);
  const parsedLog = useMemo(() => parseLog(logFile.data ?? ""), [logFile.data]);

  const logs = parsedLog.length > 0 ? parsedLog : stream.logs;
  const samples = replayed.samples.length > 0 ? replayed.samples : stream.metrics;
  const phases = replayed.phases.length > 0 ? replayed.phases : stream.phases;
  const iterations = replayed.iterations.length > 0 ? replayed.iterations : stream.iterations;
  const anomalies: AnomalyRow[] =
    replayed.anomalies.length > 0 ? replayed.anomalies : stream.anomalies;
  const outcome = verdict.data ?? stream.verdict?.summary ?? null;

  if (run.isPending) return <Spinner className="run-page__spinner" />;

  if (run.isError || run.data === undefined) {
    return (
      <EmptyState
        title="Run not found"
        message={run.error instanceof Error ? run.error.message : `No run named ${runId}.`}
      />
    );
  }

  const detail = run.data;
  const reconnecting = live && !stream.connected && !stream.ended;

  // So an operator can tell a tab holds something new without opening it.
  const tabCounts: Partial<Record<Tab, number>> = {
    artifacts: files.length,
    iterations: iterations.length,
    log: logs.length,
    notes: notes.data?.notes.length ?? 0,
  };

  return (
    <div className="run-page">
      <PageHeader
        title={detail.suite}
        actions={
          live && (
            <div className="run-page__actions">
              <Button color="amber" disabled={control.isPending} onClick={() => setPending("stop")}>
                Stop
              </Button>
              <Button color="red" disabled={control.isPending} onClick={() => setPending("abort")}>
                Abort
              </Button>
            </div>
          )
        }
      >
        <span className="run-page__id">{detail.run_id}</span>
        <DefinitionRows
          rows={[
            { label: "profile", value: detail.profile ?? "-" },
            {
              label: "campaign",
              value: detail.campaign ? (
                <Link
                  to={`/tests?view=campaigns&campaign=${encodeURIComponent(detail.campaign.key)}`}
                >
                  {detail.campaign.title}
                </Link>
              ) : (
                "-"
              ),
            },
            {
              label: "unit",
              value: detail.unit_serial ? (
                <Link to={`/units/${detail.unit_serial}`}>{detail.unit_serial}</Link>
              ) : (
                "-"
              ),
            },
            { label: "target", value: detail.target ?? "-" },
            { label: "started", value: formatTimestamp(detail.started_at) },
            {
              label: live ? "elapsed" : "duration",
              value: formatDuration(
                elapsedSeconds(detail.started_at, detail.ended_at, detail.duration_s)
              ),
            },
          ]}
        />
        {reconnecting && (
          <p className="run-page__reconnect" role="status">
            Event stream lost; reconnecting.
          </p>
        )}
        {detail.fail_reason && <p className="run-page__failure">{detail.fail_reason}</p>}
      </PageHeader>

      <VerdictBanner verdict={outcome} />

      {anomalies.length > 0 && (
        <section className="run-page__anomalies" aria-label="Anomalies">
          <h2 className="run-page__anomalies-title">
            {anomalies.length} {anomalies.length === 1 ? "anomaly" : "anomalies"}
          </h2>
          <ul>
            {anomalies.map((anomaly) => (
              <li key={anomaly.seq}>
                <span className="run-page__probe">{anomaly.probe}</span>
                <span>{anomaly.anomaly_kind}</span>
                <span className="run-page__anomaly-detail">
                  {anomaly.detail == null ? "" : JSON.stringify(anomaly.detail)}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="run-page__tabs" role="tablist" aria-label="Run views">
        {TABS.map((name) => {
          const count = tabCounts[name];
          return (
            <button
              aria-selected={tab === name}
              className={clsx("run-page__tab", tab === name && "run-page__tab--active")}
              key={name}
              onClick={() => setTab(name)}
              role="tab"
              type="button"
            >
              {name}
              {count !== undefined && count > 0 && (
                <span className="run-page__tab-count" aria-hidden="true">
                  {count}
                </span>
              )}
            </button>
          );
        })}
      </div>

      <div className="run-page__panel" role="tabpanel" aria-label={tab}>
        {tab === "overview" && (
          <div className="run-page__overview">
            <VerdictSummary verdict={outcome} summaryText={summaryFile.data ?? null}>
              <h2 className="run-page__section">Test Session</h2>
              <IterationMap
                iterations={iterations}
                phases={phases}
                onSelect={(iteration) => {
                  setSelected(iteration);
                  setTab("iterations");
                }}
              />
            </VerdictSummary>
            {manifest.data && (
              <>
                <h2 className="run-page__section">Provenance</h2>
                <DefinitionRows
                  rows={[
                    { label: "host", value: manifest.data.hostname || "-" },
                    { label: "platform", value: manifest.data.platform || "-" },
                    { label: "python", value: manifest.data.python_version || "-" },
                    { label: "commit", value: manifest.data.repo_sha ?? "-" },
                  ]}
                />
              </>
            )}
          </div>
        )}

        {tab === "log" && <LogStream lines={logs} />}
        {tab === "metrics" && (
          <MetricsChart
            key={runId}
            runId={runId}
            samples={samples}
            defaultMetrics={defaultMetrics}
          />
        )}
        {tab === "iterations" && (
          <IterationTable
            key={runId}
            runId={runId}
            iterations={iterations}
            samples={samples}
            selected={selected}
            defaultMetrics={defaultMetrics}
          />
        )}
        {tab === "artifacts" && <ArtifactList runId={runId} live={live} />}
        {tab === "notes" && (
          <NotesPanel
            busy={addNote.isPending || removeNote.isPending || notes.isPending}
            notes={notes.data?.notes ?? []}
            onAdd={(body, author) => addNote.mutateAsync({ author, body })}
            onDelete={(noteId) => removeNote.mutateAsync(noteId)}
          />
        )}
      </div>

      {pending !== null && (
        <Confirm
          onConfirm={() => {
            control.mutate(pending);
            setPending(null);
          }}
          onDismiss={() => setPending(null)}
        >
          {pending === "stop"
            ? "Ask this run to finish early? It still writes a verdict."
            : "Abort this run? It is terminated without a verdict."}
        </Confirm>
      )}
    </div>
  );
};

export default RunPage;
