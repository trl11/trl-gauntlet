import { describe, expect, it } from "vitest";

import type { SuiteOverride } from "@api/types";

import {
  initialOverrideValues,
  overrideArgv,
  overridePayload,
  validateOverrides,
} from "./overrides";

function override(partial: Partial<SuiteOverride> & { name: string }): SuiteOverride {
  return {
    choices: [],
    default: null,
    flag: `--${partial.name.replace(/_/g, "-")}`,
    help: "",
    label: "",
    maximum: null,
    minimum: null,
    type: "string",
    unit: "",
    ...partial,
  };
}

const BOUNDED = override({ name: "level", type: "number", minimum: 0, maximum: 100 });

describe("initialOverrideValues", () => {
  it("seeds each field from its declared default", () => {
    const specs = [
      override({ name: "duration_s", type: "number", default: 60 }),
      override({ name: "verbose", type: "boolean", default: true }),
      override({ name: "mode", default: "fast" }),
    ];
    expect(initialOverrideValues(specs)).toEqual({
      duration_s: "60",
      verbose: true,
      mode: "fast",
    });
  });

  it("leaves an override without a default unset", () => {
    expect(initialOverrideValues([override({ name: "mode" })])).toEqual({ mode: "" });
  });
});

describe("validateOverrides", () => {
  it("accepts an empty field, which means the suite default", () => {
    expect(validateOverrides([BOUNDED], { level: "" })).toEqual({});
  });

  it("rejects text where a number is declared", () => {
    expect(validateOverrides([BOUNDED], { level: "abc" })).toEqual({ level: "must be a number" });
  });

  it("rejects a fraction where an integer is declared", () => {
    const specs = [override({ name: "cycles", type: "integer" })];
    expect(validateOverrides(specs, { cycles: "1.5" })).toEqual({
      cycles: "must be a whole number",
    });
  });

  it("rejects a value below the declared minimum", () => {
    expect(validateOverrides([BOUNDED], { level: "-1" })).toEqual({
      level: "must be at least 0",
    });
  });

  it("rejects a value above the declared maximum", () => {
    expect(validateOverrides([BOUNDED], { level: "101" })).toEqual({
      level: "must be at most 100",
    });
  });

  it("accepts a value exactly on either bound", () => {
    expect(validateOverrides([BOUNDED], { level: "0" })).toEqual({});
    expect(validateOverrides([BOUNDED], { level: "100" })).toEqual({});
  });
});

describe("overridePayload", () => {
  it("drops unset fields and coerces the rest", () => {
    const specs = [
      override({ name: "duration_s", type: "number" }),
      override({ name: "mode" }),
      override({ name: "verbose", type: "boolean" }),
    ];
    expect(overridePayload(specs, { duration_s: "12.5", mode: "", verbose: true })).toEqual({
      duration_s: 12.5,
      verbose: true,
    });
  });
});

describe("overrideArgv", () => {
  it("renders a true boolean as the bare flag and omits a false one", () => {
    const specs = [
      override({ name: "verbose", type: "boolean" }),
      override({ name: "quiet", type: "boolean" }),
    ];
    expect(overrideArgv(specs, { verbose: true, quiet: false })).toEqual(["--verbose"]);
  });

  it("renders a value as its flag followed by the text entered", () => {
    expect(overrideArgv([BOUNDED], { level: "42" })).toEqual(["--level", "42"]);
  });
});
