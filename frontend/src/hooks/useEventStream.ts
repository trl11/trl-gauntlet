import { useEffect, useRef, useState } from "react";

import { runEventsUrl } from "@api/client";
import type {
  RunAnomalyEvent,
  RunEvent,
  RunIterationEvent,
  RunLogEvent,
  RunMetricsEvent,
  RunPhaseEvent,
  RunStatus,
  RunStatusEvent,
  RunVerdictEvent,
} from "@api/types";

/** Event names the backend publishes, plus the terminator. */
const EVENT_TYPES = [
  "anomaly",
  "end",
  "iteration",
  "log",
  "metrics",
  "phase",
  "status",
  "verdict",
] as const;

const FIRST_RETRY_MS = 500;
const MAX_RETRY_MS = 10_000;

/** Inputs to {@link useEventStream}. */
export interface UseEventStreamOptions {
  /** Run to follow. `null` closes any open stream. */
  runId: string | null | undefined;
  /** Set false to hold the stream closed without unmounting. */
  enabled?: boolean;
  /** Most recent log lines kept. Older lines are dropped. */
  maxLogLines?: number;
  /** Most recent metric samples kept. */
  maxMetricSamples?: number;
}

/** What the events have added up to so far. */
interface Accumulated {
  /** Anomalies reported by probes, oldest first. */
  anomalies: RunAnomalyEvent[];
  /** True once the backend sent `end`. No further reconnect is attempted. */
  ended: boolean;
  /** Completed iterations, oldest first. */
  iterations: RunIterationEvent[];
  /** Captured stdout, oldest first and capped at `maxLogLines`. */
  logs: RunLogEvent[];
  /** Every flattened metric name seen, sorted. */
  metricNames: string[];
  /** Metric samples, oldest first and capped at `maxMetricSamples`. */
  metrics: RunMetricsEvent[];
  /** Phases inside iterations, oldest first. */
  phases: RunPhaseEvent[];
  /** Latest run status reported over the stream. */
  status: RunStatus | null;
  /** The most recent status event, which carries argv and exit code. */
  statusEvent: RunStatusEvent | null;
  /** The verdict event, once the run has finished. */
  verdict: RunVerdictEvent | null;
}

/** Everything accumulated, plus the state of the connection carrying it. */
export interface UseEventStreamResult extends Accumulated {
  /** True while the EventSource is open. */
  connected: boolean;
  /** Last transport failure, cleared on a successful reconnect. */
  error: string | null;
}

function empty(): Accumulated {
  return {
    anomalies: [],
    ended: false,
    iterations: [],
    logs: [],
    metricNames: [],
    metrics: [],
    phases: [],
    status: null,
    statusEvent: null,
    verdict: null,
  };
}

function tail<T>(items: T[], next: T, cap: number): T[] {
  const combined = [...items, next];
  return combined.length > cap ? combined.slice(combined.length - cap) : combined;
}

function mergeNames(known: string[], values: Record<string, number>): string[] {
  const incoming = Object.keys(values);
  if (incoming.every((name) => known.includes(name))) return known;
  return [...new Set([...known, ...incoming])].sort();
}

/** Fold one event into what the stream has seen so far. */
function withEvent(
  state: Accumulated,
  event: RunEvent,
  maxLogLines: number,
  maxMetricSamples: number
): Accumulated {
  switch (event.type) {
    case "anomaly":
      return { ...state, anomalies: [...state.anomalies, event] };
    case "end":
      return { ...state, ended: true };
    case "iteration":
      return { ...state, iterations: [...state.iterations, event] };
    case "log":
      return { ...state, logs: tail(state.logs, event, maxLogLines) };
    case "metrics":
      return {
        ...state,
        metricNames: mergeNames(state.metricNames, event.values),
        metrics: tail(state.metrics, event, maxMetricSamples),
      };
    case "phase":
      return { ...state, phases: [...state.phases, event] };
    case "status":
      return { ...state, status: event.status, statusEvent: event };
    case "verdict":
      return { ...state, verdict: event };
    default:
      return state;
  }
}

/**
 * Follow one run's server-sent event stream.
 *
 * Reconnects with exponential backoff, resuming from the highest sequence
 * number already seen so a dropped connection loses nothing the backend still
 * holds in its replay ring. The `end` frame stops the stream for good.
 */
export function useEventStream(options: UseEventStreamOptions): UseEventStreamResult {
  const { runId, enabled = true, maxLogLines = 2000, maxMetricSamples = 2000 } = options;

  const [state, setState] = useState<Accumulated>(empty);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const seqRef = useRef(0);

  useEffect(() => {
    seqRef.current = 0;
    setState(empty());
    setError(null);
    setConnected(false);

    if (!runId || !enabled || typeof EventSource === "undefined") return;

    let stopped = false;
    let attempt = 0;
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const handle = (message: MessageEvent<string>) => {
      if (!message.data) return;
      let parsed: RunEvent;
      try {
        parsed = JSON.parse(message.data) as RunEvent;
      } catch {
        return;
      }
      attempt = 0;
      if (typeof parsed.seq === "number" && parsed.seq > seqRef.current) {
        seqRef.current = parsed.seq;
      }
      setState((previous) => withEvent(previous, parsed, maxLogLines, maxMetricSamples));
      if (parsed.type === "end") {
        stopped = true;
        source?.close();
        setConnected(false);
      }
    };

    const connect = () => {
      if (stopped) return;
      const stream = new EventSource(runEventsUrl(runId, seqRef.current));
      source = stream;

      stream.onopen = () => {
        attempt = 0;
        setConnected(true);
        setError(null);
      };
      stream.onmessage = handle;
      for (const type of EVENT_TYPES) {
        stream.addEventListener(type, handle as EventListener);
      }
      stream.onerror = () => {
        stream.close();
        setConnected(false);
        if (stopped) return;
        setError("event stream lost; reconnecting");
        const delay = Math.min(FIRST_RETRY_MS * 2 ** attempt, MAX_RETRY_MS);
        attempt += 1;
        retry = setTimeout(connect, delay);
      };
    };

    connect();

    return () => {
      stopped = true;
      if (retry !== null) clearTimeout(retry);
      source?.close();
      source = null;
      setConnected(false);
    };
  }, [runId, enabled, maxLogLines, maxMetricSamples]);

  return { ...state, connected, error };
}

export default useEventStream;
