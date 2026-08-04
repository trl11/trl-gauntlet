import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Confirm } from "@trl11/components/ui";
import { useState } from "react";
import { Link } from "react-router";

import { abortRun, stopRun } from "@api/client";
import type { RunRow } from "@api/types";
import StatusPill from "@components/StatusPill";
import useEventStream from "@hooks/useEventStream";
import { formatDuration, toDate } from "../utils/format";

import "./ActiveRun.scss";

/** Props for {@link ActiveRun}. */
export interface ActiveRunProps {
  /** Wall-clock milliseconds the elapsed time is measured against. */
  now: number;
  run: RunRow;
}

/** One in-flight run, with its live phase and the two ways to end it. */
export const ActiveRun: React.FC<ActiveRunProps> = ({ now, run }) => {
  const client = useQueryClient();
  const [confirming, setConfirming] = useState<"abort" | "stop" | null>(null);
  const stream = useEventStream({ runId: run.run_id, maxLogLines: 1, maxMetricSamples: 1 });
  const control = useMutation({
    mutationFn: (action: "abort" | "stop") =>
      action === "abort" ? abortRun(run.run_id) : stopRun(run.run_id),
    onSettled: () => client.invalidateQueries({ queryKey: ["runs"] }),
  });

  const started = toDate(run.started_at)?.getTime() ?? null;
  const elapsed = started === null ? null : (now - started) / 1000;
  const phase = stream.phases.at(-1);
  const iteration = stream.iterations.at(-1)?.iteration ?? phase?.iteration ?? null;

  return (
    <div className="active-run">
      <div className="active-run__head">
        <StatusPill status={stream.status ?? run.status} />
        <Link className="active-run__suite" to={`/runs/${encodeURIComponent(run.run_id)}`}>
          {run.suite}
        </Link>
        <span className="active-run__unit">{run.unit_serial ?? "no unit"}</span>
      </div>
      <dl className="active-run__facts">
        <dt>Elapsed</dt>
        <dd>{formatDuration(elapsed)}</dd>
        <dt>Phase</dt>
        <dd>{phase?.phase ?? "-"}</dd>
        <dt>Iteration</dt>
        <dd>{iteration ?? "-"}</dd>
      </dl>
      <div className="active-run__actions">
        <Button size="small" disabled={control.isPending} onClick={() => setConfirming("stop")}>
          Stop
        </Button>
        <Button
          className="active-run__abort"
          color="outline"
          size="small"
          disabled={control.isPending}
          onClick={() => setConfirming("abort")}
        >
          Abort
        </Button>
      </div>
      {control.isError && <p className="active-run__error">{(control.error as Error).message}</p>}
      {confirming && (
        <Confirm
          onConfirm={() => {
            control.mutate(confirming);
            setConfirming(null);
          }}
          onDismiss={() => setConfirming(null)}
        >
          {confirming === "stop"
            ? `Stop ${run.suite}? The suite finishes early and still writes a verdict.`
            : `Abort ${run.suite}? The process is killed and no verdict is written.`}
        </Confirm>
      )}
    </div>
  );
};

export default ActiveRun;
