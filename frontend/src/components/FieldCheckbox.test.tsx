import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import FieldCheckbox from "./FieldCheckbox";

describe("FieldCheckbox", () => {
  it("labels the box from above, so the label names it", () => {
    render(
      <FieldCheckbox
        checked={false}
        disabled={false}
        id="f"
        label="Stop on failure"
        onChange={vi.fn()}
      />
    );
    const label = screen.getByText("Stop on failure");
    expect(label.tagName).toBe("LABEL");
    expect(label).toHaveAttribute("for", "f");
    expect(screen.getByLabelText("Stop on failure")).not.toBeChecked();
  });

  it("shows the hint beneath the box", () => {
    render(
      <FieldCheckbox
        checked
        disabled={false}
        hint="Abandon the run on the first failure."
        id="f"
        label="Stop on failure"
        onChange={vi.fn()}
      />
    );
    expect(screen.getByText("Abandon the run on the first failure.")).toBeInTheDocument();
    expect(screen.getByLabelText("Stop on failure")).toBeChecked();
  });

  it("reports the new state when the box is clicked", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(
      <FieldCheckbox checked={false} disabled={false} id="f" label="Enabled" onChange={onChange} />
    );
    await user.click(screen.getByLabelText("Enabled"));
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("refuses a click while disabled", async () => {
    const user = userEvent.setup();
    const onChange = vi.fn();
    render(<FieldCheckbox checked={false} disabled id="f" label="Enabled" onChange={onChange} />);
    await user.click(screen.getByLabelText("Enabled"));
    expect(onChange).not.toHaveBeenCalled();
  });
});
