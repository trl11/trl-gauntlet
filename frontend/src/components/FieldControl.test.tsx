import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { InstrumentField } from "@api/types";

import FieldControl from "./FieldControl";

const scale: InstrumentField = {
  choices: ["1", "5", "10"],
  dial: false,
  label: "Scale",
  max: 1000,
  min: 0.001,
  name: "scale",
  type: "number",
  unit: "",
};

function Harness({ start }: { start: unknown }) {
  const [value, setValue] = useState<unknown>(start);
  return (
    <>
      <FieldControl
        disabled={false}
        field={scale}
        id="scale"
        name="Scale"
        onChange={setValue}
        state={{}}
        value={value}
      />
      <output>{String(value)}</output>
    </>
  );
}

describe("FieldControl", () => {
  it("offers a number's choices as presets without an entry", () => {
    render(<Harness start={5} />);

    expect(screen.getByLabelText("Scale preset")).toHaveValue("5");
    expect(screen.queryByRole("spinbutton")).toBeNull();
  });

  it("opens an entry for a custom number and sends what is typed", async () => {
    render(<Harness start={5} />);

    await userEvent.selectOptions(screen.getByLabelText("Scale preset"), "custom");
    const entry = screen.getByRole("spinbutton");
    await userEvent.clear(entry);
    await userEvent.type(entry, "4.7");

    expect(screen.getByRole("status")).toHaveTextContent("4.7");
  });

  it("opens the entry for a value no preset matches", () => {
    render(<Harness start={3.3} />);

    expect(screen.getByLabelText("Scale preset")).toHaveValue("custom");
    expect(screen.getByRole("spinbutton")).toHaveValue(3.3);
  });
});
