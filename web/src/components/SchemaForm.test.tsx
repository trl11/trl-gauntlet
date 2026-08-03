import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { JsonSchema } from "@api/types";

import SchemaForm from "./SchemaForm";

const SCHEMA: JsonSchema = {
  type: "object",
  title: "Profile",
  required: ["cycles"],
  properties: {
    cycles: { type: "integer", title: "Cycles", minimum: 1, default: 3 },
    soak_s: { type: "number", description: "Dwell at each setpoint" },
    label: { type: "string" },
    stop_on_failure: { type: "boolean", default: true },
    mode: { type: "string", enum: ["fast", "slow"] },
    limits: {
      type: "object",
      title: "Limits",
      properties: { max_c: { type: "number" } },
    },
  },
};

function Harness({ schema = SCHEMA }: { schema?: JsonSchema }) {
  const [value, setValue] = useState<Record<string, unknown>>({});
  return (
    <>
      <SchemaForm schema={schema} value={value} onChange={setValue} />
      <pre data-testid="state">{JSON.stringify(value)}</pre>
    </>
  );
}

function state(): Record<string, unknown> {
  return JSON.parse(screen.getByTestId("state").textContent ?? "{}");
}

describe("SchemaForm", () => {
  it("renders one control per property, offering defaults as placeholders", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Cycles")).toHaveAttribute("placeholder", "default: 3");
    expect(screen.getByLabelText("stop_on_failure")).toBeChecked();
    expect(screen.getByLabelText("mode")).toBeInTheDocument();
    expect(screen.getByText("Dwell at each setpoint")).toBeInTheDocument();
  });

  it("honours minimum and required from the schema", () => {
    render(<Harness />);
    const cycles = screen.getByLabelText("Cycles");
    expect(cycles).toHaveAttribute("min", "1");
    expect(cycles).toBeRequired();
  });

  it("reports a number as a number and a checkbox as a boolean", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(screen.getByLabelText("Cycles"), "12");
    await user.click(screen.getByLabelText("stop_on_failure"));
    expect(state()).toEqual({ cycles: 12, stop_on_failure: false });
  });

  it("writes a nested object under its own key", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(screen.getByLabelText("max_c"), "40");
    expect(state()).toEqual({ limits: { max_c: 40 } });
  });

  it("drops a key when its control is cleared, so the suite default applies", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.type(screen.getByLabelText("label"), "x");
    await user.clear(screen.getByLabelText("label"));
    expect(state()).toEqual({});
  });

  it("resolves a $ref into $defs", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: { limits: { $ref: "#/$defs/Limits" } },
      $defs: {
        Limits: { type: "object", title: "Limits", properties: { max_c: { type: "number" } } },
      },
    };
    render(<Harness schema={schema} />);
    expect(screen.getByRole("group", { name: "Limits" })).toBeInTheDocument();
    expect(screen.getByLabelText("max_c")).toBeInTheDocument();
  });

  it("unwraps an optional anyOf and keeps the outer default", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: { cycles: { anyOf: [{ type: "integer" }, { type: "null" }], default: 5 } },
    };
    render(<Harness schema={schema} />);
    const cycles = screen.getByLabelText("cycles");
    expect(cycles).toHaveAttribute("type", "number");
    expect(cycles).toHaveAttribute("placeholder", "default: 5");
  });

  it("points at YAML for a property it has no control for", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: { segments: { type: "array", items: { type: "string" } } },
    };
    render(<Harness schema={schema} />);
    expect(screen.getByText(/segments cannot be edited here/)).toBeInTheDocument();
  });

  it("points at YAML for a second level of nesting", () => {
    const schema: JsonSchema = {
      type: "object",
      properties: {
        outer: {
          type: "object",
          properties: { inner: { type: "object", properties: { deep: { type: "string" } } } },
        },
      },
    };
    render(<Harness schema={schema} />);
    expect(screen.getByText(/inner cannot be edited here/)).toBeInTheDocument();
  });

  it("explains a schema that is not an object", () => {
    render(<Harness schema={{ type: "string" }} />);
    expect(screen.getByText(/not an object/)).toBeInTheDocument();
  });
});
