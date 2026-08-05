import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import Knob from "./Knob";

describe("Knob", () => {
  function knob(onChange = vi.fn(), value = 5) {
    render(
      <Knob
        disabled={false}
        label="Voltage (V)"
        max={30}
        min={0}
        onChange={onChange}
        step={0.1}
        value={value}
      />
    );
    return onChange;
  }

  it("reports its range and its setting", () => {
    knob();
    const dial = screen.getByRole("slider", { name: "Voltage (V) dial" });

    expect(dial).toHaveAttribute("aria-valuemin", "0");
    expect(dial).toHaveAttribute("aria-valuemax", "30");
    expect(dial).toHaveAttribute("aria-valuenow", "5");
  });

  it("turns by one step on an arrow key", async () => {
    const onChange = knob();

    await userEvent.tab();
    await userEvent.keyboard("{ArrowUp}");

    expect(onChange).toHaveBeenCalledWith(5.1);
  });

  it("goes to either end of the range", async () => {
    const onChange = knob();

    await userEvent.tab();
    await userEvent.keyboard("{End}");
    await userEvent.keyboard("{Home}");

    expect(onChange).toHaveBeenNthCalledWith(1, 30);
    expect(onChange).toHaveBeenNthCalledWith(2, 0);
  });

  it("cannot be turned or reached while disabled", async () => {
    const onChange = vi.fn();
    render(
      <Knob
        disabled
        label="Voltage (V)"
        max={30}
        min={0}
        onChange={onChange}
        step={0.1}
        value={5}
      />
    );

    await userEvent.tab();
    await userEvent.keyboard("{ArrowUp}");

    expect(onChange).not.toHaveBeenCalled();
    expect(screen.getByRole("slider")).toHaveAttribute("aria-disabled", "true");
  });
});
