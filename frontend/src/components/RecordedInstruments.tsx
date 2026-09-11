import { useQuery } from "@tanstack/react-query";
import { Spinner } from "@trl11/components/ui";

import { artifactUrl, getRunInstruments } from "@api/client";
import EmptyState from "@components/EmptyState";

import "./RecordedInstruments.scss";

/** Props for {@link RecordedInstruments}. */
export interface RecordedInstrumentsProps {
  /** Run whose recording is shown. */
  runId: string;
  /**
   * Called with a reading's key, prefixed by the instrument it came from,
   * when the operator asks to see it charted on the Metrics tab.
   */
  onSelectReading: (key: string) => void;
}

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
 * trace beside this table matches on. That key, prefixed by the instrument's,
 * is also how the same reading is named on the Metrics tab's chart, which is
 * where a row sends the operator who clicks it.
 */
export const RecordedInstruments: React.FC<RecordedInstrumentsProps> = ({
  runId,
  onSelectReading,
}) => {
  const record = useQuery({
    queryKey: ["run-instruments", runId],
    queryFn: () => getRunInstruments(runId),
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
                  {instrument.readings.map((reading) => (
                    <tr key={reading.key}>
                      <td className="recorded-instruments__open-cell">
                        <button
                          type="button"
                          className="recorded-instruments__open"
                          onClick={() => onSelectReading(`${instrument.name}.${reading.key}`)}
                        >
                          <span className="recorded-instruments__label">
                            {reading.group && (
                              <span className="recorded-instruments__group">{reading.group}</span>
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
                  ))}
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
