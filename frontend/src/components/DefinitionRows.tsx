import "./DefinitionRows.scss";

/** One key and its value. */
export interface DefinitionRow {
  label: string;
  value: React.ReactNode;
}

/** Props for {@link DefinitionRows}. */
export interface DefinitionRowsProps {
  /** Rows in the order they should read. */
  rows: DefinitionRow[];
}

/** Key and value pairs: a mono lowercase key left, its value right-aligned. */
export const DefinitionRows: React.FC<DefinitionRowsProps> = ({ rows }) => (
  <dl className="definition-rows">
    {rows.map((row) => (
      <div key={row.label}>
        <dt>{row.label}</dt>
        <dd>{row.value}</dd>
      </div>
    ))}
  </dl>
);

export default DefinitionRows;
