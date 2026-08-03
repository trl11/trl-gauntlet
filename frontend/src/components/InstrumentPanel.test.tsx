import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Instrument } from "@api/types";

import InstrumentPanel from "./InstrumentPanel";

function instrument(overrides: Partial<Instrument> = {}): Instrument {
  return {
    available: true,
    commands: [
      {
        name: "set_level",
        label: "Set Level",
        fields: [
          {
            name: "channel",
            label: "Channel",
            type: "string",
            unit: "",
            min: null,
            max: null,
            choices: ["a", "b"],
          },
          {
            name: "level",
            label: "Level",
            type: "number",
            unit: "V",
            min: 0,
            max: 10,
            choices: [],
          },
          {
            name: "latch",
            label: "Latch",
            type: "boolean",
            unit: "",
            min: null,
            max: null,
            choices: [],
          },
        ],
      },
    ],
    description: "A made-up instrument.",
    instance_id: "thing0",
    kind: "thing",
    name: "thing",
    unavailable_reason: "",
    state: { armed: false, rails: { main: 4.99 } },
    ...overrides,
  };
}

describe("InstrumentPanel", () => {
  it("flattens nested state into dotted rows", () => {
    render(<InstrumentPanel instrument={instrument()} onCommand={vi.fn()} />);
    expect(screen.getByText("rails.main")).toBeInTheDocument();
    expect(screen.getByText("4.99")).toBeInTheDocument();
    expect(screen.getByText("armed")).toBeInTheDocument();
    expect(screen.getByText("false")).toBeInTheDocument();
  });

  it("renders a control for every declared field", () => {
    render(<InstrumentPanel instrument={instrument()} onCommand={vi.fn()} />);
    expect(screen.getByLabelText("Channel")).toBeInTheDocument();
    expect(screen.getByLabelText("Level (V)")).toBeInTheDocument();
    expect(screen.getByLabelText("Latch")).toBeInTheDocument();
  });

  it("posts the command with values coerced to the declared types", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={instrument()} onCommand={onCommand} />);

    await userEvent.type(screen.getByLabelText("Level (V)"), "7");
    await userEvent.click(screen.getByLabelText("Latch"));
    await userEvent.click(screen.getByRole("button", { name: "Set Level" }));

    expect(onCommand).toHaveBeenCalledWith("set_level", { channel: "a", latch: true, level: 7 });
  });

  it("says so when the instrument takes no commands", () => {
    render(<InstrumentPanel instrument={instrument({ commands: [] })} onCommand={vi.fn()} />);
    expect(screen.getByText("Takes no commands.")).toBeInTheDocument();
  });

  it("disables every control for an unavailable instrument", () => {
    render(<InstrumentPanel instrument={instrument({ available: false })} onCommand={vi.fn()} />);
    expect(screen.getByLabelText("Level (V)")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Set Level" })).toBeDisabled();
  });

  it("shows the message from a rejected command", () => {
    render(
      <InstrumentPanel instrument={instrument()} onCommand={vi.fn()} error="level out of range" />
    );
    expect(screen.getByRole("alert")).toHaveTextContent("level out of range");
  });
});

/**
 * Nothing in the panel may know an instrument by name. This one shares no
 * command, no field and no state key with the one above, and the panel has to
 * draw it from its declaration alone.
 */
const UNSEEN: Instrument = {
  available: true,
  commands: [
    {
      name: "spin_up",
      label: "Spin Up",
      fields: [
        {
          name: "rpm",
          label: "Speed",
          type: "integer",
          unit: "rpm",
          min: 0,
          max: 12000,
          choices: [],
        },
        {
          name: "direction",
          label: "Direction",
          type: "string",
          unit: "",
          min: null,
          max: null,
          choices: ["cw", "ccw"],
        },
      ],
    },
    { name: "vent", label: "Vent", fields: [] },
  ],
  description: "A centrifuge nobody has written a panel for.",
  instance_id: "spinner-7",
  kind: "centrifuge",
  name: "centrifuge",
  unavailable_reason: "",
  state: { lid: { latched: true }, rpm: 0, vacuum_mbar: 1013.25 },
};

describe("InstrumentPanel draws an instrument it has never seen", () => {
  it("renders its state, whatever the keys are called", () => {
    render(<InstrumentPanel instrument={UNSEEN} onCommand={vi.fn()} />);
    expect(screen.getByText("lid.latched")).toBeInTheDocument();
    expect(screen.getByText("vacuum_mbar")).toBeInTheDocument();
    expect(screen.getByText("1,013.25")).toBeInTheDocument();
  });

  it("renders a button per declared command and a control per declared field", () => {
    render(<InstrumentPanel instrument={UNSEEN} onCommand={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Spin Up" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Vent" })).toBeInTheDocument();
    expect(screen.getByLabelText("Speed (rpm)")).toHaveAttribute("step", "1");
    expect(screen.getAllByRole("option").map((option) => option.textContent)).toEqual(
      expect.arrayContaining(["cw", "ccw"])
    );
  });

  it("posts a command with no fields as an empty argument map", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={UNSEEN} onCommand={onCommand} />);
    await userEvent.click(screen.getByRole("button", { name: "Vent" }));
    expect(onCommand).toHaveBeenCalledWith("vent", {});
  });

  it("coerces each argument to the type the field declares", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={UNSEEN} onCommand={onCommand} />);
    await userEvent.type(screen.getByLabelText("Speed (rpm)"), "4500");
    await userEvent.selectOptions(screen.getByLabelText("Direction"), "ccw");
    await userEvent.click(screen.getByRole("button", { name: "Spin Up" }));
    expect(onCommand).toHaveBeenCalledWith("spin_up", { direction: "ccw", rpm: 4500 });
  });
});

describe("InstrumentPanel lays out declared readouts", () => {
  function withReadouts(overrides: Partial<Instrument> = {}): Instrument {
    return instrument({
      commands: [],
      connection: "/dev/ttyUSB0",
      readouts: [
        {
          group: "Channel A",
          key: "rails.main",
          label: "Main rail",
          precision: 2,
          role: "headline",
          unit: "V",
        },
        {
          group: "Channel A",
          key: "current_a",
          label: "Current",
          precision: 3,
          role: "summary",
          unit: "A",
        },
      ],
      state: { armed: false, current_a: 0.125, rails: { main: 4.987 } },
      ...overrides,
    });
  }

  it("shows a headline reading as a tile with its unit and precision", () => {
    render(<InstrumentPanel instrument={withReadouts()} onCommand={vi.fn()} />);

    expect(screen.getByText("Main rail")).toBeInTheDocument();
    expect(screen.getByText("4.99")).toBeInTheDocument();
    expect(screen.getByText("V")).toBeInTheDocument();
  });

  it("shows a summary reading in the compact strip", () => {
    render(<InstrumentPanel instrument={withReadouts()} onCommand={vi.fn()} />);

    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("0.125")).toBeInTheDocument();
  });

  it("names the group it declared", () => {
    render(<InstrumentPanel instrument={withReadouts()} onCommand={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Channel A" })).toBeInTheDocument();
  });

  it("puts the instance and the connection in the subtitle", () => {
    render(<InstrumentPanel instrument={withReadouts()} onCommand={vi.fn()} />);

    expect(screen.getByText("thing0 · /dev/ttyUSB0")).toBeInTheDocument();
  });

  it("falls back to the generic state list when nothing is declared", () => {
    render(<InstrumentPanel instrument={instrument({ readouts: [] })} onCommand={vi.fn()} />);

    expect(screen.getByText("rails.main")).toBeInTheDocument();
  });

  it("shows the reason an unavailable instrument gave", () => {
    render(
      <InstrumentPanel
        instrument={instrument({ available: false, unavailable_reason: "port is held elsewhere" })}
        onCommand={vi.fn()}
      />
    );

    expect(screen.getByText("port is held elsewhere")).toBeInTheDocument();
  });

  it("explains an unavailable instrument that gave no reason", () => {
    render(
      <InstrumentPanel
        instrument={instrument({ available: false, unavailable_reason: "" })}
        onCommand={vi.fn()}
      />
    );

    expect(screen.getByText(/controls are read-only/)).toBeInTheDocument();
  });
});

describe("InstrumentPanel arranges commands by shape", () => {
  const noFields = { name: "arm", label: "Arm", fields: [] };
  const primary = { name: "run", label: "Run", fields: [] };

  it("renders a command that takes no fields as a plain button", async () => {
    const onCommand = vi.fn();
    render(
      <InstrumentPanel instrument={instrument({ commands: [noFields] })} onCommand={onCommand} />
    );

    await userEvent.click(screen.getByRole("button", { name: "Arm" }));

    expect(onCommand).toHaveBeenCalledWith("arm", {});
  });

  it("gives the declared primary command its own control", async () => {
    const onCommand = vi.fn();
    render(
      <InstrumentPanel
        instrument={instrument({ commands: [noFields, primary], primary_command: "run" })}
        onCommand={onCommand}
      />
    );

    await userEvent.click(screen.getByRole("button", { name: "Run" }));

    expect(onCommand).toHaveBeenCalledWith("run", {});
  });

  it("disables every command while one is in flight", () => {
    render(
      <InstrumentPanel
        busy
        instrument={instrument({ commands: [noFields, primary], primary_command: "run" })}
        onCommand={vi.fn()}
      />
    );

    for (const button of screen.getAllByRole("button")) {
      expect(button).toBeDisabled();
    }
  });
});
