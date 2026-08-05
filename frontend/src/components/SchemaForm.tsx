import { Input, Select } from "@trl11/components/ui";
import { useId } from "react";

import type { JsonSchema } from "@api/types";
import FieldCheckbox from "@components/FieldCheckbox";

import "./SchemaForm.scss";

/**
 * How deep nested objects are rendered.
 *
 * One level of recursion covers the profile shapes suites publish; anything
 * deeper is left to the raw YAML editor.
 */
const MAX_DEPTH = 1;

/** The control one schema node maps to. */
type ControlKind =
  "boolean" | "choice" | "integer" | "number" | "object" | "string" | "unsupported";

function deref(node: JsonSchema, root: JsonSchema): JsonSchema {
  if (typeof node.$ref !== "string") return node;
  const name = node.$ref.slice(node.$ref.lastIndexOf("/") + 1);
  return root.$defs?.[name] ?? {};
}

/**
 * Follow a `$ref` and unwrap an optional `anyOf`.
 *
 * These are the two constructs pydantic emits for a nested model and for an
 * optional field. Everything else is read as written.
 */
function resolveNode(node: JsonSchema, root: JsonSchema): JsonSchema {
  const direct = deref(node, root);
  if (!Array.isArray(direct.anyOf)) return direct;
  const chosen = direct.anyOf
    .map((entry) => deref(entry, root))
    .find((entry) => entry.type !== "null");
  if (!chosen) return {};
  return {
    ...chosen,
    default: direct.default ?? chosen.default,
    description: direct.description ?? chosen.description,
    title: direct.title ?? chosen.title,
  };
}

/** Which control renders a resolved schema node. */
function controlKind(node: JsonSchema): ControlKind {
  if (Array.isArray(node.enum) && node.enum.every((entry) => typeof entry === "string")) {
    return "choice";
  }
  const type = Array.isArray(node.type) ? node.type[0] : node.type;
  if (type === "boolean") return "boolean";
  if (type === "integer") return "integer";
  if (type === "number") return "number";
  if (type === "string") return "string";
  if (type === "object" && node.properties) return "object";
  return "unsupported";
}

function labelFor(name: string, node: JsonSchema): string {
  return typeof node.title === "string" && node.title ? node.title : name;
}

function asRecord(value: unknown): Record<string, unknown> {
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return {};
}

function textOf(value: unknown): string {
  if (value === undefined || value === null) return "";
  return String(value);
}

/**
 * What an unset field shows.
 *
 * A property absent from the profile takes the schema's default, so the
 * default is offered as a placeholder rather than filled in as a value.
 */
function placeholderFor(node: JsonSchema): string {
  return node.default == null ? "(unset)" : `default: ${String(node.default)}`;
}

interface FieldProps {
  disabled: boolean;
  fieldId: string;
  name: string;
  node: JsonSchema;
  onChange: (next: unknown) => void;
  required: boolean;
  root: JsonSchema;
  value: unknown;
  depth: number;
}

const Field: React.FC<FieldProps> = ({
  depth,
  disabled,
  fieldId,
  name,
  node,
  onChange,
  required,
  root,
  value,
}) => {
  const kind = controlKind(node);
  const label = labelFor(name, node);
  const hint = typeof node.description === "string" ? node.description : undefined;

  if (kind === "object" && depth < MAX_DEPTH) {
    return (
      <fieldset className="schema-form__group">
        <legend className="schema-form__legend">{label}</legend>
        {hint && <p className="schema-form__hint">{hint}</p>}
        <ObjectFields
          depth={depth + 1}
          disabled={disabled}
          fieldId={fieldId}
          node={node}
          onChange={(next) => onChange(next)}
          root={root}
          value={asRecord(value)}
        />
      </fieldset>
    );
  }

  if (kind === "boolean") {
    const checked = value === undefined ? node.default === true : value === true;
    return (
      <FieldCheckbox
        id={fieldId}
        label={label}
        hint={hint}
        checked={checked}
        disabled={disabled}
        onChange={onChange}
      />
    );
  }

  if (kind === "choice") {
    const options = [
      { value: "", label: placeholderFor(node) },
      ...(node.enum ?? []).map((entry) => ({ value: String(entry), label: String(entry) })),
    ];
    return (
      <Select
        id={fieldId}
        label={label}
        hint={hint}
        options={options}
        value={textOf(value)}
        disabled={disabled}
        required={required}
        onChange={(event) => onChange(event.target.value || undefined)}
      />
    );
  }

  if (kind === "integer" || kind === "number") {
    return (
      <Input
        id={fieldId}
        label={label}
        hint={hint}
        type="number"
        step={kind === "integer" ? 1 : "any"}
        min={node.minimum}
        max={node.maximum}
        placeholder={placeholderFor(node)}
        value={textOf(value)}
        disabled={disabled}
        required={required}
        onChange={(event) => {
          const text = event.target.value;
          if (text === "") {
            onChange(undefined);
            return;
          }
          const parsed = Number(text);
          onChange(Number.isFinite(parsed) ? parsed : text);
        }}
      />
    );
  }

  if (kind === "string") {
    return (
      <Input
        id={fieldId}
        label={label}
        hint={hint}
        type="text"
        placeholder={placeholderFor(node)}
        value={textOf(value)}
        disabled={disabled}
        required={required}
        onChange={(event) => onChange(event.target.value || undefined)}
      />
    );
  }

  return (
    <p className="schema-form__unsupported">
      {label} cannot be edited here. Switch to YAML to change it.
    </p>
  );
};

interface ObjectFieldsProps {
  depth: number;
  disabled: boolean;
  fieldId: string;
  node: JsonSchema;
  onChange: (next: Record<string, unknown>) => void;
  root: JsonSchema;
  value: Record<string, unknown>;
}

const ObjectFields: React.FC<ObjectFieldsProps> = ({
  depth,
  disabled,
  fieldId,
  node,
  onChange,
  root,
  value,
}) => {
  const required = new Set(node.required ?? []);
  const properties = Object.entries(node.properties ?? {});

  const update = (key: string, next: unknown) => {
    const merged = { ...value };
    if (next === undefined) delete merged[key];
    else merged[key] = next;
    onChange(merged);
  };

  return (
    <div className="schema-form__fields">
      {properties.map(([key, child]) => (
        <Field
          key={key}
          depth={depth}
          disabled={disabled}
          fieldId={`${fieldId}-${key}`}
          name={key}
          node={resolveNode(child, root)}
          onChange={(next) => update(key, next)}
          required={required.has(key)}
          root={root}
          value={value[key]}
        />
      ))}
    </div>
  );
};

/** Props for {@link SchemaForm}. */
export interface SchemaFormProps {
  disabled?: boolean;
  /** Called with the whole object every time a control changes. */
  onChange: (next: Record<string, unknown>) => void;
  /** The schema document. `$ref`s resolve against its own `$defs`. */
  schema: JsonSchema;
  /** Current values, keyed by property name. */
  value: Record<string, unknown>;
}

/**
 * A form generated from a JSON Schema object.
 *
 * Renders one control per declared property and recurses once into a nested
 * object. A property it has no control for is marked in place, pointing the
 * operator at the raw YAML editor.
 */
export const SchemaForm: React.FC<SchemaFormProps> = ({
  disabled = false,
  onChange,
  schema,
  value,
}) => {
  const fieldId = useId();
  const root = resolveNode(schema, schema);

  if (controlKind(root) !== "object") {
    return (
      <p className="schema-form__unsupported">
        This profile schema is not an object. Switch to YAML to edit it.
      </p>
    );
  }

  return (
    <div className="schema-form">
      <ObjectFields
        depth={0}
        disabled={disabled}
        fieldId={fieldId}
        node={root}
        onChange={onChange}
        root={schema}
        value={value}
      />
    </div>
  );
};

export default SchemaForm;
