import { faTriangleExclamation } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useIsFetching, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { API_BASE, ApiError } from "@api/client";

import "./ApiErrorBanner.scss";

/** How long the backend must stay unreachable before the banner appears. */
const DELAY_MS = 5000;

function describeTarget(): string {
  if (API_BASE) return API_BASE;
  return typeof window === "undefined" ? "the backend" : window.location.host;
}

/**
 * Tells the operator when the backend has stopped answering.
 *
 * Watches the react-query cache rather than any single query, so it reports a
 * genuine outage instead of one endpoint returning a 404.
 */
export const ApiErrorBanner: React.FC = () => {
  const client = useQueryClient();
  const fetching = useIsFetching();
  const [failingSince, setFailingSince] = useState<number | null>(null);
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const cache = client.getQueryCache();
    const recompute = () => {
      const unreachable = cache.getAll().some((entry) => {
        if (entry.state.status !== "error" || entry.state.fetchStatus !== "idle") return false;
        const error = entry.state.error;
        return !(error instanceof ApiError) || error.status === 0 || error.status >= 500;
      });
      if (unreachable) {
        setFailingSince((previous) => previous ?? Date.now());
      } else {
        setFailingSince(null);
        setVisible(false);
      }
    };
    recompute();
    // The cache notifies while another component is still rendering — mounting
    // a query is what notifies it — so recomputing inline would set state
    // during that render. One microtask later the render has committed, and
    // several notifications in a row collapse into a single pass.
    let queued = false;
    return cache.subscribe(() => {
      if (queued) return;
      queued = true;
      queueMicrotask(() => {
        queued = false;
        recompute();
      });
    });
  }, [client]);

  useEffect(() => {
    if (failingSince === null) return;
    const remaining = DELAY_MS - (Date.now() - failingSince);
    if (remaining <= 0) {
      setVisible(true);
      return;
    }
    const timer = setTimeout(() => setVisible(true), remaining);
    return () => clearTimeout(timer);
  }, [failingSince]);

  if (!visible) return null;

  return (
    <div className="api-error-banner" role="status" aria-live="polite">
      <FontAwesomeIcon icon={faTriangleExclamation} aria-hidden="true" />
      <span>
        {`Gauntlet at ${describeTarget()} is not responding`}
        {fetching > 0 ? "; reconnecting" : ""}
      </span>
    </div>
  );
};

export default ApiErrorBanner;
