import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";
import { useParams } from "react-router";

import { getDaqRecording, getRun } from "@api/client";
import DaqViewer from "@components/DaqViewer";
import EmptyState from "@components/EmptyState";
import useDaq from "@hooks/useDaq";
import useEventStream from "@hooks/useEventStream";
import { isLive } from "../utils/run_status";

import "./DaqWindowPage.scss";

/** How often the run is refreshed while it is in flight. */
const LIVE_POLL_MS = 2000;

/**
 * A run's DAQ graphs and controls and nothing else, for a window of its own.
 *
 * It is outside the page layout on purpose: no navigation, no run details.
 */
export const DaqWindowPage: React.FC = () => {
  const { runId = "" } = useParams<{ runId: string }>();
  const run = useQuery({
    queryKey: ["run", runId],
    queryFn: () => getRun(runId),
    refetchInterval: (query) => (isLive(query.state.data?.status) ? LIVE_POLL_MS : false),
  });
  const live = isLive(run.data?.status);
  const stream = useEventStream({ runId, enabled: live });
  const { flashing, upsets } = useDaq(runId, live, stream.upsets.length);
  // Shared with the viewer's own query, so it costs no second request.
  const recording = useQuery({
    queryKey: ["daq-recording", runId],
    queryFn: () => getDaqRecording(runId),
  });

  if (run.isPending || upsets.isPending || recording.isPending)
    return <Spinner className="daq-window__spinner" />;
  if (run.isError || !upsets.data) {
    return <EmptyState title="Run not found" message={`There is no run ${runId}.`} />;
  }

  return (
    <div className="daq-window">
      <h1 className="daq-window__title">
        {run.data?.suite} <span className="daq-window__status">{run.data?.status}</span>
      </h1>
      {upsets.data.instruments.length === 0 &&
      upsets.data.events.length === 0 &&
      (recording.data?.instruments.length ?? 0) === 0 ? (
        <EmptyState
          title="No DAQ"
          message="This run is not watching a DAQ and recorded no events."
        />
      ) : (
        <DaqViewer flashing={flashing} live={live} runId={runId} summary={upsets.data} />
      )}
    </div>
  );
};

export default DaqWindowPage;
