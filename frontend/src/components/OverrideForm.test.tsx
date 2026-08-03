import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { SuiteOverride } from "@api/types";

import OverrideForm from "./OverrideForm";
import { validateOverrides, type OverrideValues } from "../utils/overrides";

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

/** One of every type the contract declares, with bounds on the numeric ones. */
const OVERRIDES: SuiteOverride[] = [
  override({ name: "duration_s", type: "number", label: "Duration", unit: "s", minimum: 0.1 }),
  override({ name: "cycles", type: "integer", minimum: 1, maximum: 10 }),
  override({ name: "stop_on_failure", type: "boolean" }),
  override({ name: "note", type: "string", help: "Free text" }),
  override({ name: "mode", type: "string", choices: ["fast", "slow"] }),
];

function Harness({ errors = {} }: { errors?: Record<string, string> }) {
  const [values, setValues] = useState<OverrideValues>({ stop_on_failure: true });
  return (
    <>
      <OverrideForm overrides={OVERRIDES} values={values} errors={errors} onChange={setValues} />
      <pre data-testid="state">{JSON.stringify(values)}</pre>
    </>
  );
}

/** The form wired to the real validator, as the run dialog wires it. */
function ValidatingHarness() {
  const [values, setValues] = useState<OverrideValues>({ stop_on_failure: true });
  const errors = validateOverrides(OVERRIDES, values);
  return (
    <>
      <OverrideForm overrides={OVERRIDES} values={values} errors={errors} onChange={setValues} />
      <pre data-testid="errors">{JSON.stringify(errors)}</pre>
    </>
  );
}

describe("OverrideForm", () => {
  it("renders one control per declared override, honouring label, unit and help", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Duration")).toHaveAttribute("type", "number");
    expect(screen.getByText("in s")).toBeInTheDocument();
    expect(screen.getByText("Free text")).toBeInTheDocument();
    expect(screen.getByLabelText("stop_on_failure")).toBeChecked();
    expect(screen.getByLabelText("mode")).toBeInTheDocument();
  });

  it("steps a whole number by one and a real number by any amount", () => {
    render(<Harness />);
    expect(screen.getByLabelText("cycles")).toHaveAttribute("step", "1");
    expect(screen.getByLabelText("Duration")).toHaveAttribute("step", "any");
  });

  it("offers the declared choices plus the suite default", () => {
    render(<Harness />);
    const options = screen.getAllByRole("option").map((option) => option.textContent);
    expect(options).toEqual(["(suite default)", "fast", "slow"]);
  });

  it("reports every edit as the whole value map", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByLabelText("stop_on_failure"));
    await user.selectOptions(screen.getByLabelText("mode"), "slow");
    expect(JSON.parse(screen.getByTestId("state").textContent ?? "{}")).toEqual({
      mode: "slow",
      stop_on_failure: false,
    });
  });

  it("shows the validation message the caller passes in", () => {
    render(<Harness errors={{ cycles: "must be a whole number" }} />);
    expect(screen.getByText("must be a whole number")).toBeInTheDocument();
  });

  it("renders nothing when the suite declares no overrides", () => {
    const { container } = render(<OverrideForm overrides={[]} values={{}} onChange={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});

/**
 * `type="number"` inputs reject anything unparsable before React sees it, so
 * "must be a number" is only reachable through `validateOverrides` itself and
 * is covered in `utils/overrides.test.ts`.
 */
describe("OverrideForm validates what is typed into it", () => {
  it("rejects a fraction in a whole-number field", async () => {
    const user = userEvent.setup();
    render(<ValidatingHarness />);
    await user.type(screen.getByLabelText("cycles"), "1.5");
    expect(screen.getByText("must be a whole number")).toBeInTheDocument();
  });

  it("rejects a value below the declared minimum", async () => {
    const user = userEvent.setup();
    render(<ValidatingHarness />);
    await user.type(screen.getByLabelText("Duration"), "0.01");
    expect(screen.getByText("must be at least 0.1")).toBeInTheDocument();
  });

  it("rejects a value above the declared maximum", async () => {
    const user = userEvent.setup();
    render(<ValidatingHarness />);
    await user.type(screen.getByLabelText("cycles"), "99");
    expect(screen.getByText("must be at most 10")).toBeInTheDocument();
  });

  it("accepts a value inside the declared bounds", async () => {
    const user = userEvent.setup();
    render(<ValidatingHarness />);
    await user.type(screen.getByLabelText("cycles"), "5");
    await user.type(screen.getByLabelText("Duration"), "2.5");
    expect(screen.getByTestId("errors")).toHaveTextContent("{}");
  });

  it("accepts an empty field, which leaves the suite default in place", async () => {
    const user = userEvent.setup();
    render(<ValidatingHarness />);
    await user.type(screen.getByLabelText("cycles"), "99");
    await user.clear(screen.getByLabelText("cycles"));
    expect(screen.getByTestId("errors")).toHaveTextContent("{}");
  });

  it("leaves the non-numeric types alone", async () => {
    const user = userEvent.setup();
    render(<ValidatingHarness />);
    await user.type(screen.getByLabelText("note"), "anything at all");
    await user.selectOptions(screen.getByLabelText("mode"), "slow");
    await user.click(screen.getByLabelText("stop_on_failure"));
    expect(screen.getByTestId("errors")).toHaveTextContent("{}");
  });
});
