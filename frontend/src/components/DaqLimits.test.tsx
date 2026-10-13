import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import type { StreamChannel } from "@api/types";
import { defaultSettings } from "../utils/daq_limits";
import DaqLimits from "./DaqLimits";

const CHANNELS: StreamChannel[] = [
  { key: "1", label: "Rail", max: 10, min: -10, unit: "V" },
  { key: "2", label: "Aux", max: 5, min: -5, unit: "V" },
];

const Harness: React.FC<{ selectable?: boolean }> = ({ selectable = false }) => {
  const [value, setValue] = useState(defaultSettings(CHANNELS));
  return (
    <DaqLimits
      channels={CHANNELS}
      idPrefix="t"
      onChange={setValue}
      selectable={selectable}
      value={value}
    />
  );
};

describe("DaqLimits", () => {
  it("fills each channel with the range the instrument can read", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Rail low limit")).toHaveValue("-10");
    expect(screen.getByLabelText("Rail high limit")).toHaveValue("10");
    expect(screen.getByLabelText("Aux high limit")).toHaveValue("5");
  });

  it("takes a limit typed over the default", async () => {
    render(<Harness />);
    const high = screen.getByLabelText("Rail high limit");
    await userEvent.clear(high);
    await userEvent.type(high, "3.3");
    expect(high).toHaveValue("3.3");
    expect(screen.getByLabelText("Aux high limit")).toHaveValue("5");
  });

  it("offers no checkbox unless channels can be enabled", () => {
    render(<Harness />);
    expect(screen.queryByRole("checkbox")).not.toBeInTheDocument();
  });

  it("lets a channel be turned off, which takes its limits out of reach", async () => {
    render(<Harness selectable />);
    const box = screen.getByLabelText("Aux enabled");
    expect(box).toBeChecked();
    await userEvent.click(box);
    expect(box).not.toBeChecked();
    expect(screen.getByLabelText("Aux low limit")).toBeDisabled();
    expect(screen.getByLabelText("Rail low limit")).toBeEnabled();
  });

  it("offers the window either side of a crossing", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Before (s)")).toHaveValue("2");
    expect(screen.getByLabelText("After (s)")).toHaveValue("2");
  });
});
