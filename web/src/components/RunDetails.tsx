import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";

import { getRunVerdict } from "@api/client";
import type { RunRow, Verdict } from "@api/types";

import "./RunDetails.scss";

/** Props for {@link RunDetails}. */
export interface RunDetailsProps {
  run: RunRow;
}

/** The test counters behind a run, or why there are none to show. */
function testCounts(loading: boolean, verdict: Verdict | undefined): string {
  if (loading) return "loading";
  if (verdict === undefined) return "no verdict recorded";
  return `${verdict.successes} passed, ${verdict.failures} failed, ${verdict.total_iterations} iterations`;
}

/** The verdict figures behind one run, for an expanded history row. */
export const RunDetails: React.FC<RunDetailsProps> = ({ run }) => {
  const verdict = useQuery({
    queryKey: ["run-verdict", run.run_id],
    queryFn: () => getRunVerdict(run.run_id),
    retry: false,
  });

  return (
    <dl className="run-details">
      <dt>Run</dt>
      <dd>
        <Link to={`/runs/${encodeURIComponent(run.run_id)}`}>{run.run_id}</Link>
      </dd>
      <dt>Verdict</dt>
      <dd>{run.verdict ?? "-"}</dd>
      <dt>Reason</dt>
      <dd>{run.fail_reason || verdict.data?.reason || "-"}</dd>
      <dt>Tests</dt>
      <dd>{testCounts(verdict.isPending, verdict.data)}</dd>
      <dt>Target</dt>
      <dd>{run.target ?? "-"}</dd>
      <dt>Artifacts</dt>
      <dd className="run-details__path">{run.run_dir}</dd>
    </dl>
  );
};

export default RunDetails;
