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

  it("gives a field that declares a range a dial beside its entry", () => {
    render(<InstrumentPanel instrument={instrument()} onCommand={vi.fn()} />);
    expect(screen.getByRole("slider", { name: "Level (V) dial" })).toBeInTheDocument();
  });

  it("posts the setting the dial was turned to", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={instrument()} onCommand={onCommand} />);

    await userEvent.click(screen.getByRole("slider", { name: "Level (V) dial" }));
    await userEvent.keyboard("{End}");
    await userEvent.click(screen.getByRole("button", { name: "Set Level" }));

    expect(onCommand).toHaveBeenCalledWith("set_level", { channel: "a", latch: false, level: 10 });
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

describe("InstrumentPanel latches a primary command that settles one boolean", () => {
  const output = {
    danger: true,
    fields: [
      {
        name: "enabled",
        label: "Enabled",
        type: "boolean" as const,
        unit: "",
        min: null,
        max: null,
        choices: [],
      },
    ],
    label: "Set Output",
    name: "set_output",
  };

  function supply(overrides: Partial<Instrument> = {}): Instrument {
    return instrument({ commands: [output], primary_command: "set_output", ...overrides });
  }

  const key = () => screen.getByRole("button", { name: /Set Output/ });
  const lock = () => screen.getByRole("switch", { name: "Lock" });

  it("draws one key rather than a toggle and a send key", () => {
    render(<InstrumentPanel instrument={supply()} onCommand={vi.fn()} />);

    expect(key()).toBeInTheDocument();
    expect(screen.queryByLabelText("Enabled")).not.toBeInTheDocument();
  });

  it("cannot be pressed until the lock is released", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={supply()} onCommand={onCommand} />);

    expect(key()).toBeDisabled();

    await userEvent.click(lock());
    await userEvent.click(key());

    expect(onCommand).toHaveBeenCalledWith("set_output", { enabled: true });
  });

  it("stays as the operator left it, pressed or not", async () => {
    render(<InstrumentPanel instrument={supply()} onCommand={vi.fn()} />);

    await userEvent.click(lock());
    await userEvent.click(key());

    expect(key()).toBeEnabled();
    expect(lock()).toHaveAttribute("aria-checked", "false");

    await userEvent.click(lock());

    expect(key()).toBeDisabled();
  });

  it("sends the opposite of what it last sent", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={supply()} onCommand={onCommand} />);

    await userEvent.click(lock());
    await userEvent.click(key());
    await userEvent.click(key());

    expect(onCommand).toHaveBeenNthCalledWith(1, "set_output", { enabled: true });
    expect(onCommand).toHaveBeenNthCalledWith(2, "set_output", { enabled: false });
    expect(key()).toHaveAttribute("aria-pressed", "false");
  });

  it("still sends whatever else the command asks for", async () => {
    const onCommand = vi.fn();
    const channel = {
      name: "channel",
      label: "Channel",
      type: "string" as const,
      unit: "",
      min: null,
      max: null,
      choices: ["1", "2"],
    };
    render(
      <InstrumentPanel
        instrument={supply({ commands: [{ ...output, fields: [channel, ...output.fields] }] })}
        onCommand={onCommand}
      />
    );

    await userEvent.selectOptions(screen.getByLabelText("Channel"), "2");
    await userEvent.click(lock());
    await userEvent.click(key());

    expect(onCommand).toHaveBeenCalledWith("set_output", { channel: "2", enabled: true });
  });

  it("keeps the lock shut while a run is driving the instrument", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={supply({ in_use_by: "run-7" })} onCommand={onCommand} />);

    expect(lock()).toBeDisabled();
    expect(key()).toBeDisabled();
    expect(screen.getByText(/run run-7 is driving this instrument/)).toBeInTheDocument();
  });

  it("leaves the key locked behind a run that took the instrument", async () => {
    const { rerender } = render(<InstrumentPanel instrument={supply()} onCommand={vi.fn()} />);

    await userEvent.click(lock());
    expect(key()).toBeEnabled();

    rerender(<InstrumentPanel instrument={supply({ in_use_by: "run-7" })} onCommand={vi.fn()} />);
    expect(key()).toBeDisabled();

    rerender(<InstrumentPanel instrument={supply()} onCommand={vi.fn()} />);
    expect(key()).toBeDisabled();
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

describe("InstrumentPanel, a command that settles several things at once", () => {
  const configure = {
    name: "configure",
    label: "Apply",
    row_label: "Channel",
    rows: [
      { key: "1", label: "CH 1", values: { mode: "10v", label: "Rail 3V3" } },
      { key: "2", label: "CH 2", values: { mode: "5v", label: "" } },
    ],
    fields: [
      {
        name: "mode",
        label: "Mode",
        type: "string" as const,
        unit: "",
        min: null,
        max: null,
        choices: ["10v", "5v", "tc_k"],
      },
      {
        name: "label",
        label: "Label",
        type: "string" as const,
        unit: "",
        min: null,
        max: null,
        choices: [],
      },
    ],
  };

  const rowwise = (onCommand = vi.fn()) => {
    render(
      <InstrumentPanel instrument={instrument({ commands: [configure] })} onCommand={onCommand} />
    );
    return onCommand;
  };

  it("runs the things settled across and the fields down", () => {
    rowwise();
    // Eight channels would otherwise be eight rows of controls tall.
    expect(screen.getByRole("columnheader", { name: "Channel" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "CH 1" })).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "CH 2" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Mode" })).toBeInTheDocument();
    expect(screen.getByRole("rowheader", { name: "Label" })).toBeInTheDocument();
  });

  it("starts each control at what that row is set to now", () => {
    rowwise();
    expect(screen.getByRole("combobox", { name: "CH 1 Mode" })).toHaveValue("10v");
    expect(screen.getByRole("combobox", { name: "CH 2 Mode" })).toHaveValue("5v");
    expect(screen.getByRole("textbox", { name: "CH 1 Label" })).toHaveValue("Rail 3V3");
    expect(screen.getByRole("textbox", { name: "CH 2 Label" })).toHaveValue("");
  });

  it("sends every row at once, edited and untouched alike", async () => {
    const onCommand = rowwise();

    await userEvent.selectOptions(screen.getByRole("combobox", { name: "CH 2 Mode" }), "tc_k");
    await userEvent.type(screen.getByRole("textbox", { name: "CH 2 Label" }), "Ambient");
    await userEvent.click(screen.getByRole("button", { name: "Apply" }));

    expect(onCommand).toHaveBeenCalledWith("configure", {
      rows: {
        "1": { mode: "10v", label: "Rail 3V3" },
        "2": { mode: "tc_k", label: "Ambient" },
      },
    });
  });

  it("keeps a row-wise command off the latching key", async () => {
    // The key stands for one boolean, and a table has as many as it has rows.
    const onCommand = vi.fn();
    render(
      <InstrumentPanel
        instrument={instrument({ commands: [configure], primary_command: "configure" })}
        onCommand={onCommand}
      />
    );
    expect(screen.queryByRole("switch", { name: "Lock" })).toBeNull();
    expect(screen.getByRole("button", { name: "Apply" })).toBeInTheDocument();
  });
});
