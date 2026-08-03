import { formatNumber } from "../utils/format";

import "./InstrumentState.scss";

/** Props for {@link InstrumentState}. */
export interface InstrumentStateProps {
  state: Record<string, unknown>;
}

function formatValue(value: unknown): string {
  if (value === null || value === undefined) return "-";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "number") return formatNumber(value);
  if (Array.isArray(value)) return value.map(formatValue).join(", ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

/** Flatten nested state into dotted paths so any shape renders as a table. */
function stateRows(state: Record<string, unknown>): Array<[string, string]> {
  const rows: Array<[string, string]> = [];
  const walk = (value: unknown, path: string) => {
    if (value !== null && typeof value === "object" && !Array.isArray(value)) {
      for (const [key, nested] of Object.entries(value)) {
        walk(nested, path ? `${path}.${key}` : key);
      }
      return;
    }
    rows.push([path, formatValue(value)]);
  };
  walk(state, "");
  return rows;
}

/**
 * Every value an instrument reports, as a flat table of dotted keys.
 *
 * This is what a provider that declares no readouts gets, so any state shape
 * is still readable without the provider saying anything about layout.
 */
export const InstrumentState: React.FC<InstrumentStateProps> = ({ state }) => {
  const rows = stateRows(state);
  if (rows.length === 0) return <p className="instrument-state__quiet">Reports no state.</p>;
  return (
    <dl className="instrument-state">
      {rows.map(([key, value]) => (
        <div key={key}>
          <dt>{key}</dt>
          <dd>{value}</dd>
        </div>
      ))}
    </dl>
  );
};

export default InstrumentState;
