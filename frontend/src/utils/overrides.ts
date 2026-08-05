/**
 * Reading and validating the per-run knobs a suite declares.
 *
 * Everything here works off `overrides[]` in the manifest, so no code knows
 * any particular suite.
 */

import { parse } from "yaml";

import type { SuiteOverride } from "@api/types";

/**
 * What the operator has entered, keyed by override name.
 *
 * Numbers are held as the raw text so a half-typed value survives a keystroke
 * and so validation can report what was actually entered.
 */
export type OverrideValues = Record<string, boolean | string>;

/** Does this override take a number rather than text or a flag. */
export function isNumeric(override: SuiteOverride): boolean {
  return override.type === "integer" || override.type === "number";
}

/**
 * The top-level fields of a profile, or nothing when it will not parse.
 *
 * A profile the operator is about to run is not theirs to fix here, so a
 * broken one yields no defaults rather than an error.
 */
export function profileFields(body: string): Record<string, unknown> {
  try {
    const parsed: unknown = parse(body);
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    return parsed as Record<string, unknown>;
  } catch {
    return {};
  }
}

/** A profile field an override control can hold: a scalar, never a nested block. */
function scalarField(value: unknown): boolean | number | string | null {
  const usable =
    typeof value === "boolean" || typeof value === "number" || typeof value === "string";
  return usable ? (value as boolean | number | string) : null;
}

/**
 * The value each override starts at.
 *
 * An override sets the profile field of the same name, so the profile's own
 * value is what the run would use and is what the operator is shown. An
 * override the profile says nothing about falls back to the manifest's
 * declared default, and then to empty, which also means the suite decides.
 */
export function initialOverrideValues(
  overrides: SuiteOverride[],
  profile: Record<string, unknown> = {}
): OverrideValues {
  const values: OverrideValues = {};
  for (const override of overrides) {
    const declared = scalarField(profile[override.name]) ?? override.default;
    if (override.type === "boolean") {
      values[override.name] = declared === true;
    } else {
      values[override.name] = declared == null ? "" : String(declared);
    }
  }
  return values;
}

/** The problem with one entered value, or null when it is acceptable. */
function problemWith(override: SuiteOverride, text: string): string | null {
  if (!isNumeric(override) || text === "") return null;
  const parsed = Number(text);
  if (!Number.isFinite(parsed)) return "must be a number";
  if (override.type === "integer" && !Number.isInteger(parsed)) return "must be a whole number";
  if (override.minimum !== null && parsed < override.minimum) {
    return `must be at least ${override.minimum}`;
  }
  if (override.maximum !== null && parsed > override.maximum) {
    return `must be at most ${override.maximum}`;
  }
  return null;
}

/**
 * Problems with the entered values, keyed by override name.
 *
 * An empty result means the run can be submitted. Nothing is required: an
 * empty field means the suite's own default applies.
 */
export function validateOverrides(
  overrides: SuiteOverride[],
  values: OverrideValues
): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const override of overrides) {
    const problem = problemWith(override, String(values[override.name] ?? "").trim());
    if (problem !== null) errors[override.name] = problem;
  }
  return errors;
}

/** The `overrides` object to send with `POST /api/runs`. Unset fields are dropped. */
export function overridePayload(
  overrides: SuiteOverride[],
  values: OverrideValues
): Record<string, unknown> {
  const payload: Record<string, unknown> = {};
  for (const override of overrides) {
    const value = values[override.name];
    if (override.type === "boolean") {
      payload[override.name] = value === true;
      continue;
    }
    const text = String(value ?? "").trim();
    if (text === "") continue;
    payload[override.name] = isNumeric(override) ? Number(text) : text;
  }
  return payload;
}

/**
 * The argv fragment the entered overrides contribute.
 *
 * Mirrors what the launcher builds: a boolean is the bare flag when true and
 * nothing when false, everything else is the flag followed by its value.
 */
export function overrideArgv(overrides: SuiteOverride[], values: OverrideValues): string[] {
  const argv: string[] = [];
  for (const override of overrides) {
    const value = values[override.name];
    if (override.type === "boolean") {
      if (value === true) argv.push(override.flag);
      continue;
    }
    const text = String(value ?? "").trim();
    if (text !== "") argv.push(override.flag, text);
  }
  return argv;
}
