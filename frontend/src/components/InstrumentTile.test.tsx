import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { describe, expect, it } from "vitest";

import type { Instrument } from "@api/types";

import InstrumentTile from "./InstrumentTile";

function instrument(overrides: Partial<Instrument> = {}): Instrument {
  return {
    available: true,
    commands: [],
    description: "A made-up instrument.",
    instance_id: "thing0",
    kind: "supply",
    name: "thing",
    readouts: [
      { group: "", key: "voltage", label: "Voltage", precision: 2, role: "headline", unit: "V" },
      { group: "", key: "current", label: "Current", precision: 3, role: "headline", unit: "A" },
      { group: "", key: "power", label: "Power", precision: 1, role: "summary", unit: "W" },
    ],
    state: { current: 0.5, power: 6, voltage: 12 },
    unavailable_reason: "",
    ...overrides,
  };
}

/** An instrument declaring ``count`` headline readings, as a DAQ's channels. */
function channels(count: number): Instrument {
  return instrument({
    readouts: Array.from({ length: count }, (_, at) => ({
      group: "Analog",
      key: `channels.${at + 1}.value`,
      label: `CH ${at + 1}`,
      precision: 4,
      role: "headline" as const,
      unit: "V",
    })),
    state: {
      channels: Object.fromEntries(
        Array.from({ length: count }, (_, at) => [String(at + 1), { value: at + 1 }])
      ),
    },
  });
}

function draw(entry: Instrument) {
  return render(
    <MemoryRouter>
      <InstrumentTile instrument={entry} />
    </MemoryRouter>
  );
}

describe("InstrumentTile", () => {
  it("burns each headline reading at the precision the provider asked for", () => {
    draw(instrument());
    expect(screen.getByText("12.00")).toBeInTheDocument();
    expect(screen.getByText("0.500")).toBeInTheDocument();
    expect(screen.getByText("Voltage")).toBeInTheDocument();
    expect(screen.getByText("V")).toBeInTheDocument();
  });

  it("leaves the summary readings to the full panel", () => {
    draw(instrument());
    expect(screen.queryByText("Power")).toBeNull();
  });

  it("reads an instrument that declares no readouts from its state", () => {
    draw(instrument({ readouts: [], state: { output_enabled: true, rails: { main: 5 } } }));
    expect(screen.getByText("output enabled")).toBeInTheDocument();
    expect(screen.getByText("on")).toBeInTheDocument();
    // Nested state is not a reading a display can burn.
    expect(screen.queryByText("rails")).toBeNull();
  });

  it("shows why an instrument is unavailable in place of its readings", () => {
    draw(instrument({ available: false, unavailable_reason: "no reply from the bus" }));
    expect(screen.getByText("no reply from the bus")).toBeInTheDocument();
    expect(screen.queryByText("12.00")).toBeNull();
  });

  it("says an instrument is unavailable when the provider gives no reason", () => {
    draw(instrument({ available: false }));
    expect(screen.getByText("unavailable")).toBeInTheDocument();
  });

  it("names the kind beside the instrument, unless the name already says it", () => {
    const { unmount } = draw(instrument());
    expect(screen.getByText("supply")).toBeInTheDocument();
    unmount();

    draw(instrument({ kind: "thing" }));
    expect(screen.getAllByText("thing")).toHaveLength(1);
  });

  it("shows every channel of an eight-channel instrument", () => {
    draw(channels(8));
    for (let channel = 1; channel <= 8; channel += 1) {
      expect(screen.getByText(`CH ${channel}`)).toBeInTheDocument();
    }
  });

  it("takes a second column once it holds more readings than fit two-by-two", () => {
    const { container } = draw(channels(8));
    expect(container.querySelector(".instrument-tile--wide")).not.toBeNull();
  });

  it("stays one column wide while its readings fit", () => {
    const { container } = draw(channels(4));
    expect(container.querySelector(".instrument-tile--wide")).toBeNull();
  });

  it("opens the instruments page, where the instrument can be driven", () => {
    draw(instrument());
    expect(screen.getByRole("link", { name: "thing, available" })).toHaveAttribute(
      "href",
      "/instruments"
    );
  });
});
