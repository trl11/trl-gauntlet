import { describe, expect, it } from "vitest";

import type { InstrumentCommand, InstrumentField } from "@api/types";

import {
  coerce,
  dialled,
  fieldLabel,
  formatFieldValue,
  initialArgs,
  initialValue,
  isHexField,
  latchField,
  runtimeChoices,
  stepOf,
} from "./commandFields";

function makeField(overrides: Partial<InstrumentField>): InstrumentField {
  return {
    choices: [],
    label: "",
    max: null,
    min: null,
    name: "value",
    type: "string",
    unit: "",
    ...overrides,
  };
}

function makeCommand(fields: InstrumentField[]): InstrumentCommand {
  return { fields, label: "", name: "set" };
}

const addressField = makeField({
  choices_from: "found",
  format: "hex",
  name: "address",
  type: "integer",
});

describe("runtimeChoices", () => {
  it("offers a hex field's discovered values as bare hex", () => {
    expect(runtimeChoices(addressField, { found: [16, 72, 5] })).toEqual(["10", "48", "05"]);
  });

  it("offers nothing when the state holds no list under that key", () => {
    expect(runtimeChoices(addressField, {})).toEqual([]);
    expect(runtimeChoices(addressField, { found: "16" })).toEqual([]);
  });

  it("offers nothing for a field that names no source", () => {
    expect(runtimeChoices(makeField({}), { found: [1] })).toEqual([]);
  });
});

describe("formatFieldValue", () => {
  it("writes a number as decimal unless the field says hex", () => {
    expect(formatFieldValue(255, makeField({ type: "integer" }))).toBe("255");
    expect(formatFieldValue(255, addressField)).toBe("ff");
  });

  it("leaves text in a hex field as it came", () => {
    expect(formatFieldValue("0x10", addressField)).toBe("0x10");
  });
});

describe("isHexField", () => {
  it("is true only for a numeric field formatted as hex", () => {
    expect(isHexField(addressField)).toBe(true);
    expect(isHexField(makeField({ format: "hex", type: "string" }))).toBe(false);
    expect(isHexField(makeField({ type: "integer" }))).toBe(false);
  });
});

describe("fieldLabel", () => {
  it("prefers the label and appends the unit", () => {
    expect(fieldLabel(makeField({ label: "Voltage", name: "volts", unit: "V" }))).toBe(
      "Voltage (V)"
    );
  });

  it("falls back to the name when no label is declared", () => {
    expect(fieldLabel(makeField({ name: "channel" }))).toBe("channel");
  });
});

describe("dialled", () => {
  it("gives a ranged number a dial", () => {
    expect(dialled(makeField({ max: 30, min: 0, type: "number" }))).toBe(true);
  });

  it("gives no dial to text, an open range, an empty range or a field that opts out", () => {
    expect(dialled(makeField({ max: 30, min: 0 }))).toBe(false);
    expect(dialled(makeField({ max: null, min: 0, type: "number" }))).toBe(false);
    expect(dialled(makeField({ max: 5, min: 5, type: "number" }))).toBe(false);
    expect(dialled(makeField({ dial: false, max: 30, min: 0, type: "number" }))).toBe(false);
  });
});

describe("stepOf", () => {
  it("divides the range into between a hundred and a thousand settings", () => {
    expect(stepOf(makeField({ max: 30, min: 0, type: "number" }))).toBe(0.1);
    expect(stepOf(makeField({ max: 12000, min: 0, type: "number" }))).toBe(100);
  });

  it("never steps an integer field by less than one", () => {
    expect(stepOf(makeField({ max: 10, min: 0, type: "integer" }))).toBe(1);
  });

  it("steps by one when there is no range to divide", () => {
    expect(stepOf(makeField({ type: "number" }))).toBe(1);
  });
});

describe("latchField", () => {
  const enable = makeField({ name: "enabled", type: "boolean" });

  it("finds the one boolean of a command that otherwise only picks from choices", () => {
    const channel = makeField({ choices: ["1", "2"], name: "channel" });
    expect(latchField(makeCommand([channel, enable]))).toBe(enable);
  });

  it("finds none when a free-form field sits beside the boolean", () => {
    expect(latchField(makeCommand([makeField({ name: "note" }), enable]))).toBeNull();
  });

  it("finds none when there are no booleans or more than one", () => {
    expect(latchField(makeCommand([]))).toBeNull();
    expect(latchField(makeCommand([enable, makeField({ name: "b", type: "boolean" })]))).toBeNull();
  });
});

describe("initialValue", () => {
  it("starts a boolean off, a choice at its first, a dial at its lowest and text empty", () => {
    expect(initialValue(makeField({ type: "boolean" }))).toBe(false);
    expect(initialValue(makeField({ choices: ["a", "b"] }))).toBe("a");
    expect(initialValue(makeField({ max: 30, min: 2, type: "number" }))).toBe(2);
    expect(initialValue(makeField({}))).toBe("");
  });

  it("builds a starting value for every field by name", () => {
    const fields = [makeField({ name: "on", type: "boolean" }), makeField({ name: "note" })];
    expect(initialArgs(fields)).toEqual({ note: "", on: false });
  });
});

describe("coerce", () => {
  it("reads hex text as a number", () => {
    expect(coerce(addressField, "4a")).toBe(74);
  });

  it("sends zero for hex text that does not parse", () => {
    expect(coerce(addressField, "zz")).toBe(0);
  });

  it("converts decimal text, booleans and strings to their declared type", () => {
    expect(coerce(makeField({ type: "number" }), "2.5")).toBe(2.5);
    expect(coerce(makeField({ type: "boolean" }), "true")).toBe(false);
    expect(coerce(makeField({ type: "boolean" }), true)).toBe(true);
    expect(coerce(makeField({}), undefined)).toBe("");
    expect(coerce(makeField({}), 7)).toBe("7");
  });
});
