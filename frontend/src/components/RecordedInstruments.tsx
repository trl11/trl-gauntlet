import { faChevronDown, faChevronRight } from "@fortawesome/free-solid-svg-icons";
import { FontAwesomeIcon } from "@fortawesome/react-fontawesome";
import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";
import { Fragment, useState } from "react";
import {
  CartesianGrid,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip as ChartTooltip,
  XAxis,
  YAxis,
} from "recharts";

import { artifactUrl, getRunInstruments, getRunInstrumentTrace } from "@api/client";
import type { RecordedTick } from "@api/types";
import EmptyState from "@components/EmptyState";
import { formatNumber, formatTimestamp } from "../utils/format";
import { paddedDomain } from "../utils/metrics";

import "./RecordedInstruments.scss";

/** Props for {@link RecordedInstruments}. */
export interface RecordedInstrumentsProps {
  /** Run whose recording is shown. */
  runId: string;
}

/** Props for {@link ReadingChart}. */
interface ReadingChartProps {
  /** Instance key of the instrument the reading came from. */
  instrument: string;
  /** Key the reading is recorded under. */
  reading: string;
  /** Every line of `instruments.jsonl`. */
  trace: RecordedTick[];
}

/** One reading over the run, drawn from the instruments' trace. */
const ReadingChart: React.FC<ReadingChartProps> = ({ instrument, reading, trace }) => {
  const rows = trace
    .filter((tick) => tick.instrument === instrument && reading in tick.values)
    .map((tick) => ({ at: tick.at, value: tick.values[reading], x: tick.t }));

  if (rows.length === 0) {
    return (
      <p className="recorded-instruments__silent">The trace holds no samples of this reading.</p>
    );
  }

  const atOf = new Map(rows.map((row) => [row.x, row.at]));

  return (
    <div className="recorded-instruments__chart">
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={rows} margin={{ top: 4, right: 12, bottom: 4 }}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis
            dataKey="x"
            domain={["dataMin", "dataMax"]}
            tickFormatter={(value: number) => `${formatNumber(value, 1)}s`}
            type="number"
          />
          <YAxis
            allowDataOverflow
            domain={paddedDomain(rows.map((row) => row.value))}
            tickFormatter={(value: number) => formatNumber(value)}
            width={64}
          />
          <ChartTooltip
            formatter={(value: number | string) => formatNumber(Number(value))}
            labelFormatter={(value: number | string) =>
              `${formatNumber(Number(value), 2)} s · ${formatTimestamp(atOf.get(Number(value)))}`
            }
          />
          <Line
            dataKey="value"
            dot={false}
            isAnimationActive={false}
            name={reading}
            stroke="currentColor"
            strokeWidth={1.5}
            type="monotone"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
};

/** A reading to as many decimals as its instrument asked for. */
function show(value: number, precision: number | null): string {
  return value.toFixed(precision ?? 3);
}

/**
 * What the bench's instruments read while a run was in flight.
 *
 * The summary is a file the run left behind, so this asks nothing of the
 * instruments and reads the same for a run finished months ago. Nothing here
 * knows an instrument: every row is a reading the provider published.
 *
 * A reading is shown under the name its channel carried during the run and
 * over the key it is recorded as. The name is the operator's and changes with
 * the bench; the key is the same everywhere, and is what anyone reading the
 * trace beside this table matches on. Clicking a row expands it to chart that
 * reading from `instruments.jsonl`.
 */
export const RecordedInstruments: React.FC<RecordedInstrumentsProps> = ({ runId }) => {
  const [open, setOpen] = useState<string | null>(null);
  const record = useQuery({
    queryKey: ["run-instruments", runId],
    queryFn: () => getRunInstruments(runId),
  });
  // Shares its key with the run page's query, so the Metrics tab and this one
  // read the trace once between them.
  const trace = useQuery({
    queryKey: ["run-instrument-trace", runId],
    queryFn: () => getRunInstrumentTrace(runId),
    enabled: open !== null,
    retry: false,
  });

  if (record.isPending) return <Spinner className="recorded-instruments__spinner" />;

  if (record.isError || record.data === undefined) {
    return (
      <EmptyState
        title="Nothing recorded"
        message="This run did not record its instruments, or the summary has not been written yet."
      />
    );
  }

  const { instruments, interval_s, ticks } = record.data;

  return (
    <div className="recorded-instruments">
      <p className="recorded-instruments__note">
        Read every {interval_s}s, {ticks} {ticks === 1 ? "time" : "times"} over the run.{" "}
        <a href={artifactUrl(runId, "instruments.jsonl")}>Download the trace</a>
      </p>

      {instruments.map((instrument) => (
        <section key={instrument.name} className="recorded-instruments__instrument">
          <h3 className="recorded-instruments__name">
            {instrument.name}
            {instrument.description && (
              <span className="recorded-instruments__description">{instrument.description}</span>
            )}
          </h3>
          {instrument.readings.length === 0 ? (
            <p className="recorded-instruments__silent">
              This instrument published no readings while the run was in flight.
            </p>
          ) : (
            <div className="recorded-instruments__scroll">
              <table className="recorded-instruments__table">
                <thead>
                  <tr>
                    <th>Reading</th>
                    <th>Min</th>
                    <th>Mean</th>
                    <th>Max</th>
                    <th>Last</th>
                    <th>Samples</th>
                  </tr>
                </thead>
                <tbody>
                  {instrument.readings.map((reading) => {
                    const id = `${instrument.name}.${reading.key}`;
                    const expanded = open === id;
                    return (
                      <Fragment key={reading.key}>
                        <tr className={expanded ? "recorded-instruments__row--open" : undefined}>
                          <td className="recorded-instruments__open-cell">
                            <button
                              type="button"
                              className="recorded-instruments__open"
                              aria-expanded={expanded}
                              onClick={() => setOpen(expanded ? null : id)}
                            >
                              <FontAwesomeIcon
                                className="recorded-instruments__caret"
                                icon={expanded ? faChevronDown : faChevronRight}
                              />
                              <span className="recorded-instruments__label">
                                {reading.group && (
                                  <span className="recorded-instruments__group">
                                    {reading.group}
                                  </span>
                                )}
                                {reading.label}
                                {reading.unit && (
                                  <span className="recorded-instruments__unit">{reading.unit}</span>
                                )}
                              </span>
                              {reading.label !== reading.key && (
                                <span className="recorded-instruments__key">{reading.key}</span>
                              )}
                            </button>
                          </td>
                          <td className="mono">{show(reading.min, reading.precision)}</td>
                          <td className="mono">{show(reading.mean, reading.precision)}</td>
                          <td className="mono">{show(reading.max, reading.precision)}</td>
                          <td className="mono">{show(reading.last, reading.precision)}</td>
                          <td className="mono">{reading.count}</td>
                        </tr>
                        {expanded && (
                          <tr className="recorded-instruments__chart-row">
                            <td colSpan={6}>
                              {trace.isPending && <Spinner />}
                              {trace.isError && (
                                <p className="recorded-instruments__silent">
                                  This run kept no trace to chart the reading from.
                                </p>
                              )}
                              {trace.isSuccess && (
                                <ReadingChart
                                  instrument={instrument.name}
                                  reading={reading.key}
                                  trace={trace.data}
                                />
                              )}
                            </td>
                          </tr>
                        )}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>
      ))}
    </div>
  );
};

export default RecordedInstruments;
