import type { InstrumentCommand, InstrumentField } from "@api/types";

/**
 * Values a field's `choices_from` names in the instrument's state, if any,
 * each written the way the field itself is entered — a hex field's picks are
 * bare hex with no `0x`, so clicking one drops straight into the entry beside
 * it exactly as if it had been typed.
 */
export function runtimeChoices(field: InstrumentField, state: Record<string, unknown>): string[] {
  if (!field.choices_from) return [];
  const found = state[field.choices_from];
  if (!Array.isArray(found)) return [];
  return found.map((value) => formatFieldValue(value, field));
}

/**
 * A raw value as the field's `format` says to both type and show it.
 *
 * `"hex"` is bare hex with no `0x`, the way an address is written on a
 * datasheet — what a hex field's entry holds is this text, not a decimal
 * string, so a pick and a typed entry are the same representation.
 */
export function formatFieldValue(value: unknown, field: InstrumentField): string {
  if (field.format === "hex" && typeof value === "number") {
    return value.toString(16).padStart(2, "0");
  }
  return String(value);
}

/** Does this field's entry read and write hex text rather than decimal. */
export function isHexField(field: InstrumentField): boolean {
  return field.format === "hex" && (field.type === "integer" || field.type === "number");
}

/** The label to show for a field, with its unit when it declares one. */
export function fieldLabel(field: InstrumentField): string {
  const name = field.label || field.name;
  return field.unit ? `${name} (${field.unit})` : name;
}

/** Is this a number the operator can dial in, rather than only type. */
export function dialled(field: InstrumentField): boolean {
  const numeric = field.type === "integer" || field.type === "number";
  return (
    numeric &&
    field.dial !== false &&
    field.min !== null &&
    field.max !== null &&
    field.max > field.min
  );
}

/**
 * How far one turn of the dial moves a field.
 *
 * Whatever the range, a power of ten is chosen that divides it into somewhere
 * between a hundred and a thousand settings, which is about as fine as a dial
 * can be driven by hand. An integer field never steps by less than one.
 */
export function stepOf(field: InstrumentField): number {
  const range = (field.max ?? 0) - (field.min ?? 0);
  const step =
    range > 0 ? Number(Math.pow(10, Math.floor(Math.log10(range / 100))).toPrecision(1)) : 1;
  return field.type === "integer" ? Math.max(1, Math.round(step)) : step;
}

/**
 * The one boolean a latching key drives, for a command shaped to have one.
 *
 * A command that settles a single true-or-false, and otherwise only picks
 * which thing to settle it for, is what a front panel gives a key that stays
 * down. Anything else keeps its controls and its send key.
 */
export function latchField(command: InstrumentCommand): InstrumentField | null {
  const booleans = command.fields.filter((field) => field.type === "boolean");
  const rest = command.fields.filter((field) => field.type !== "boolean");
  if (booleans.length !== 1) return null;
  if (rest.some((field) => field.choices.length === 0)) return null;
  return booleans[0];
}

/** The value a field starts at: its lowest setting, its first choice, or empty. */
export function initialValue(field: InstrumentField): unknown {
  if (field.type === "boolean") return false;
  if (field.choices.length > 0) return field.choices[0];
  if (dialled(field)) return field.min;
  return "";
}

export function initialArgs(fields: InstrumentField[]): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  for (const field of fields) args[field.name] = initialValue(field);
  return args;
}

/** Coerce the raw control value to the type the field declares. */
export function coerce(field: InstrumentField, value: unknown): unknown {
  if (field.type === "boolean") return value === true;
  if (field.type === "integer" || field.type === "number") {
    if (isHexField(field)) {
      const parsed = parseInt(String(value), 16);
      return Number.isNaN(parsed) ? 0 : parsed;
    }
    return Number(value);
  }
  return String(value ?? "");
}
