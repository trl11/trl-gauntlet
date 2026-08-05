import { describe, expect, it } from "vitest";

import type { SuiteOverride } from "@api/types";

import {
  initialOverrideValues,
  overrideArgv,
  overridePayload,
  profileFields,
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

  it("prefers the profile's value over the declared default", () => {
    const specs = [
      override({ name: "duration_s", type: "number", default: 60 }),
      override({ name: "verbose", type: "boolean", default: true }),
      override({ name: "mode", default: "fast" }),
    ];
    const profile = { duration_s: 300, mode: "slow", verbose: false };
    expect(initialOverrideValues(specs, profile)).toEqual({
      duration_s: "300",
      verbose: false,
      mode: "slow",
    });
  });

  it("keeps the declared default for a field the profile omits", () => {
    const specs = [override({ name: "mode", default: "fast" })];
    expect(initialOverrideValues(specs, { other: "x" })).toEqual({ mode: "fast" });
  });

  it("ignores a profile field that is a nested block rather than a value", () => {
    const specs = [override({ name: "link", default: "eth0" })];
    expect(initialOverrideValues(specs, { link: { iface: "can0" } })).toEqual({ link: "eth0" });
  });
});

describe("profileFields", () => {
  it("reads the top-level fields of a profile", () => {
    expect(profileFields("duration_s: 60\nlink:\n  iface: can0\n")).toEqual({
      duration_s: 60,
      link: { iface: "can0" },
    });
  });

  it("yields nothing for a profile that will not parse", () => {
    expect(profileFields("duration_s: [1, 2\n")).toEqual({});
  });

  it("yields nothing for a profile that is not a mapping", () => {
    expect(profileFields("- one\n- two\n")).toEqual({});
    expect(profileFields("")).toEqual({});
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
