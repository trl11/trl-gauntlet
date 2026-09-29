import { fireEvent, render, screen } from "@testing-library/react";
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

  it("moves ten steps at a time on page keys, and ignores other keys", async () => {
    const onChange = knob();

    await userEvent.tab();
    await userEvent.keyboard("{PageUp}");
    await userEvent.keyboard("{PageDown}");
    await userEvent.keyboard("a");

    expect(onChange).toHaveBeenCalledTimes(2);
    expect(onChange).toHaveBeenNthCalledWith(1, 6);
    expect(onChange).toHaveBeenNthCalledWith(2, 4);
  });

  describe("under the pointer", () => {
    // jsdom has no PointerEvent and lays nothing out, so the dial is given a
    // box and the pointer is moved with mouse events under the pointer names.
    function point(target: Element, type: string, x: number, y: number): void {
      fireEvent(target, new MouseEvent(type, { bubbles: true, clientX: x, clientY: y }));
    }

    function box(): void {
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
        new DOMRect(0, 0, 100, 100)
      );
    }

    it("turns to where it is pressed and follows a drag until released", () => {
      box();
      const onChange = knob();
      const dial = screen.getByRole("slider");

      point(dial, "pointerdown", 50, 0);
      expect(onChange).toHaveBeenLastCalledWith(15);

      point(dial, "pointermove", 100, 100);
      expect(onChange).toHaveBeenLastCalledWith(30);

      point(dial, "pointerup", 100, 100);
      point(dial, "pointermove", 0, 100);
      expect(onChange).toHaveBeenCalledTimes(2);
    });

    it("ignores the pointer while disabled", () => {
      box();
      const onChange = vi.fn();
      render(
        <Knob disabled label="Level" max={30} min={0} onChange={onChange} step={0.1} value={5} />
      );
      const dial = screen.getByRole("slider");

      point(dial, "pointerdown", 50, 0);
      point(dial, "pointermove", 100, 100);

      expect(onChange).not.toHaveBeenCalled();
    });
  });
});
