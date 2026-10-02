import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

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

describe("InstrumentPanel shows an image a command answered with", () => {
  const shot = { src: "data:image/png;base64,AAA" };

  function camera(overrides: Partial<Instrument> = {}): Instrument {
    return instrument({
      commands: [
        {
          name: "snapshot",
          label: "Take Snapshot",
          fields: [
            {
              name: "max_width",
              label: "Resolution",
              type: "string",
              unit: "px",
              min: null,
              max: null,
              choices: ["Full", "960"],
            },
          ],
          returns: "image",
        },
      ],
      primary_command: "snapshot",
      ...overrides,
    });
  }

  const mode = (name: string) => screen.getByRole("button", { name });

  it("offers the viewer from the declaration, before any image has arrived", () => {
    render(<InstrumentPanel instrument={camera()} onCommand={vi.fn()} />);

    expect(mode("Capture")).toBeInTheDocument();
    expect(mode("Continuous")).toBeInTheDocument();
    expect(screen.queryByRole("img")).not.toBeInTheDocument();
  });

  it("takes the command out of the deck, so its button is not drawn twice", () => {
    render(<InstrumentPanel instrument={camera()} onCommand={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Take Snapshot" })).not.toBeInTheDocument();
  });

  it("leaves a command pinned to the viewer in the deck when there is no viewer", () => {
    const noViewer = instrument({
      commands: [{ name: "reset", label: "Reboot Camera", fields: [], role: "viewer" }],
      primary_command: "reset",
    });
    render(<InstrumentPanel instrument={noViewer} onCommand={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Reboot Camera" })).toBeInTheDocument();
  });

  it("locks a command pinned to the viewer while a run holds the instrument", () => {
    const held = camera({
      commands: [
        ...camera().commands,
        { name: "reset", label: "Reboot Camera", fields: [], role: "viewer" },
      ],
      in_use_by: "20260904T004719Z-6baa",
    });
    render(<InstrumentPanel instrument={held} onCommand={vi.fn()} />);

    expect(mode("Reboot Camera")).toBeDisabled();
  });

  it("draws a command pinned to the viewer beside the modes rather than in the deck", async () => {
    const onCommand = vi.fn();
    const pinned = camera({
      commands: [
        ...camera().commands,
        { name: "reset", label: "Reboot Camera", fields: [], role: "viewer" },
      ],
    });
    render(<InstrumentPanel instrument={pinned} onCommand={onCommand} />);

    const button = mode("Reboot Camera");
    expect(button.closest(".instrument-panel__modes")).not.toBeNull();

    await userEvent.click(button);
    expect(onCommand).toHaveBeenCalledWith("reset", {});
  });

  it("captures once a press in snapshot mode, and draws what came back", async () => {
    const onCommand = vi.fn();
    const { rerender } = render(<InstrumentPanel instrument={camera()} onCommand={onCommand} />);

    await userEvent.click(mode("Capture"));
    expect(onCommand).toHaveBeenCalledWith("snapshot", { max_width: "Full" });

    rerender(<InstrumentPanel instrument={camera()} onCommand={onCommand} preview={shot} />);
    expect(screen.getByRole("img")).toHaveAttribute("src", shot.src);
    expect(onCommand).toHaveBeenCalledTimes(1);
  });

  it("names the button for what pressing it does in each mode", async () => {
    render(<InstrumentPanel instrument={camera()} onCommand={vi.fn()} preview={shot} />);

    expect(mode("Capture")).toBeInTheDocument();

    await userEvent.click(mode("Continuous"));
    expect(mode("Start")).toBeInTheDocument();

    await userEvent.click(mode("Start"));
    expect(mode("Stop")).toBeInTheDocument();
  });

  it("goes back to single captures when snapshot is picked again", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={camera()} onCommand={onCommand} preview={shot} />);

    await userEvent.click(mode("Continuous"));
    await userEvent.click(mode("Start"));
    await userEvent.click(mode("Snapshot"));

    expect(mode("Snapshot")).toHaveAttribute("aria-pressed", "true");
    expect(mode("Capture")).toBeInTheDocument();
  });

  it("shows a reading pinned to the viewer beside its controls", () => {
    const withPinned = camera({
      readouts: [
        {
          group: "",
          key: "sensor.temp_c",
          label: "Sensor",
          precision: 1,
          role: "viewer",
          unit: "°C",
        },
      ],
      state: { sensor: { temp_c: 41.26 } },
    });
    render(<InstrumentPanel instrument={withPinned} onCommand={vi.fn()} />);

    const reading = screen.getByText("Sensor").closest(".instrument-panel__pinned");
    expect(reading).toHaveTextContent("Sensor41.3 °C");
    expect(reading?.closest(".instrument-panel__modes")).not.toBeNull();
  });

  it("asks for the next image only once the last one has arrived", async () => {
    const onCommand = vi.fn();
    const { rerender } = render(
      <InstrumentPanel instrument={camera()} onCommand={onCommand} preview={shot} />
    );

    await userEvent.click(mode("Continuous"));
    await userEvent.click(mode("Start"));
    expect(onCommand).toHaveBeenCalledTimes(1);

    rerender(<InstrumentPanel busy instrument={camera()} onCommand={onCommand} preview={shot} />);
    expect(onCommand).toHaveBeenCalledTimes(1);

    rerender(
      <InstrumentPanel
        instrument={camera()}
        onCommand={onCommand}
        preview={{ src: "data:image/png;base64,BBB" }}
      />
    );
    expect(onCommand).toHaveBeenCalledTimes(2);
  });

  it("stops when the operator says so", async () => {
    const onCommand = vi.fn();
    const { rerender } = render(
      <InstrumentPanel instrument={camera()} onCommand={onCommand} preview={shot} />
    );

    await userEvent.click(mode("Continuous"));
    await userEvent.click(mode("Start"));
    await userEvent.click(mode("Stop"));

    rerender(
      <InstrumentPanel
        instrument={camera()}
        onCommand={onCommand}
        preview={{ src: "data:image/png;base64,BBB" }}
      />
    );
    expect(onCommand).toHaveBeenCalledTimes(1);
  });

  it("stops the loop when a command fails", async () => {
    const onCommand = vi.fn();
    const { rerender } = render(
      <InstrumentPanel instrument={camera()} onCommand={onCommand} preview={shot} />
    );

    await userEvent.click(mode("Continuous"));
    await userEvent.click(mode("Start"));

    rerender(
      <InstrumentPanel
        error="camera: no frame within 5s"
        instrument={camera()}
        onCommand={onCommand}
        preview={shot}
      />
    );

    expect(mode("Start")).toBeInTheDocument();
  });

  it("sends the preset the operator picked", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={camera()} onCommand={onCommand} />);

    await userEvent.selectOptions(screen.getByLabelText("Resolution (px)"), "960");
    await userEvent.click(mode("Capture"));

    expect(onCommand).toHaveBeenCalledWith("snapshot", { max_width: "960" });
  });

  it("tells the provider the picture is being refreshed, not kept", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={camera()} onCommand={onCommand} preview={shot} />);

    await userEvent.click(mode("Continuous"));
    await userEvent.click(mode("Start"));

    expect(onCommand).toHaveBeenCalledWith("snapshot", { live: true, max_width: "Full" });
  });

  it("takes the image off the panel when it is hidden", async () => {
    const onDismiss = vi.fn();
    render(
      <InstrumentPanel
        instrument={camera()}
        onCommand={vi.fn()}
        onDismiss={onDismiss}
        preview={shot}
      />
    );

    await userEvent.click(mode("Hide"));

    expect(onDismiss).toHaveBeenCalled();
  });

  it("offers nothing to hide until there is an image", () => {
    render(<InstrumentPanel instrument={camera()} onCommand={vi.fn()} onDismiss={vi.fn()} />);

    expect(screen.queryByRole("button", { name: "Hide" })).not.toBeInTheDocument();
  });
});

describe("InstrumentPanel draws a group's refresh in its heading", () => {
  function withRefresh(): Instrument {
    return instrument({
      commands: [{ name: "link_status", label: "Read Link Status", fields: [], refreshes: "Link" }],
      readouts: [
        {
          group: "Link",
          key: "link.errors",
          label: "Link errors",
          precision: null,
          role: "headline",
          unit: "",
        },
      ],
      state: { link: { errors: 0 } },
    });
  }

  it("sends the command from the heading rather than from a button of its own", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={withRefresh()} onCommand={onCommand} />);

    expect(screen.queryByRole("button", { name: "Read Link Status" })).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Refresh Link" }));

    expect(onCommand).toHaveBeenCalledWith("link_status", {});
  });

  it("keeps a command that takes arguments in the deck", () => {
    render(
      <InstrumentPanel
        instrument={instrument({
          commands: [
            {
              name: "sample",
              label: "Sample",
              refreshes: "Link",
              fields: [
                {
                  name: "count",
                  label: "Count",
                  type: "integer",
                  unit: "",
                  min: 1,
                  max: 9,
                  choices: [],
                },
              ],
            },
          ],
        })}
        onCommand={vi.fn()}
      />
    );

    expect(screen.getByRole("button", { name: "Sample" })).toBeInTheDocument();
  });
});

describe("InstrumentPanel reports what is holding the instrument", () => {
  it("reads as available when nothing is driving it", () => {
    render(<InstrumentPanel instrument={instrument()} onCommand={vi.fn()} />);

    expect(screen.getByText("AVAILABLE")).toBeInTheDocument();
  });

  it("reads as in use while a run is driving it, and says which run", () => {
    render(<InstrumentPanel instrument={instrument({ in_use_by: "run-7" })} onCommand={vi.fn()} />);

    expect(screen.queryByText("AVAILABLE")).not.toBeInTheDocument();
    expect(screen.getByText("IN USE")).toHaveAttribute("title", expect.stringContaining("run-7"));
  });

  it("reports hardware that has gone as unavailable, whatever holds it", () => {
    render(
      <InstrumentPanel
        instrument={instrument({ available: false, in_use_by: "run-7" })}
        onCommand={vi.fn()}
      />
    );

    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.queryByText("IN USE")).not.toBeInTheDocument();
  });
});

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

  it("keeps a number's place for a sign, so the digits do not move as it crosses zero", () => {
    const { container, rerender } = render(
      <InstrumentPanel
        instrument={withReadouts({ state: { rails: { main: 4.987 } } })}
        onCommand={vi.fn()}
      />
    );
    const cells = () =>
      container.querySelectorAll(".instrument-panel__readings .seven-segment__cell").length;
    const positive = cells();
    rerender(
      <InstrumentPanel
        instrument={withReadouts({ state: { rails: { main: -4.987 } } })}
        onCommand={vi.fn()}
      />
    );
    expect(cells()).toBe(positive);
  });

  it("dims a reading the instrument has no value for", () => {
    const { container } = render(
      <InstrumentPanel
        instrument={withReadouts({ state: { rails: { main: null } } })}
        onCommand={vi.fn()}
      />
    );
    expect(container.querySelector(".instrument-panel__reading--idle")).not.toBeNull();
  });

  it("does not dim one it has", () => {
    const { container } = render(
      <InstrumentPanel instrument={withReadouts()} onCommand={vi.fn()} />
    );
    expect(container.querySelector(".instrument-panel__reading--idle")).toBeNull();
  });

  it("shows a summary reading in the compact strip", () => {
    render(<InstrumentPanel instrument={withReadouts()} onCommand={vi.fn()} />);

    expect(screen.getByText("Current")).toBeInTheDocument();
    expect(screen.getByText("0.125")).toBeInTheDocument();
  });

  it("lifts a group with nothing to burn large into the identifying detail", () => {
    render(
      <InstrumentPanel
        instrument={withReadouts({
          readouts: [
            {
              group: "Format",
              key: "format.width",
              label: "Width",
              precision: null,
              role: "summary",
              unit: "px",
            },
            {
              group: "Channel A",
              key: "rails.main",
              label: "Main rail",
              precision: 2,
              role: "headline",
              unit: "V",
            },
          ],
          state: { format: { width: 3840 }, rails: { main: 4.987 } },
        })}
        onCommand={vi.fn()}
      />
    );

    // The spec strip carries it as text, so no display of its own is drawn.
    expect(screen.getByText("3840 px")).toBeInTheDocument();
    expect(screen.queryByText("Format")).not.toBeInTheDocument();
    expect(screen.getByText("Channel A")).toBeInTheDocument();
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

    // The collapse toggle is a local view control, not a command, so it stays
    // usable while a command is in flight.
    for (const button of screen.getAllByRole("button")) {
      if (button.className.includes("instrument-panel__collapse")) continue;
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

  describe("beside the keys that apply it", () => {
    const rate = {
      name: "scan_rate",
      label: "Scan rate",
      role: "header" as const,
      selected: "scan.selected",
      current: "scan.rate_hz",
      unit: "Hz",
      fields: [
        {
          name: "rate",
          label: "Scan rate",
          type: "string" as const,
          unit: "",
          min: null,
          max: null,
          choices: [],
          choices_from: "scan.choices",
        },
      ],
    };
    const sample = { name: "sample", label: "Sample", fields: [] };
    const scan = { choices: ["auto", "25", "12.5"], rate_hz: 25, selected: "auto" };

    const withCompanions = (onCommand = vi.fn(), primary_command = "") => {
      render(
        <InstrumentPanel
          instrument={instrument({
            commands: [sample, rate, configure],
            primary_command,
            state: { scan },
          })}
          onCommand={onCommand}
        />
      );
      return onCommand;
    };

    it("shows the setting in the header, with what it is now and a drop-down to change it", () => {
      withCompanions();
      const choice = screen.getByRole("combobox", { name: "Scan rate" });
      expect(choice).toHaveValue("auto");
      expect(choice.closest(".instrument-panel__head")).not.toBeNull();
      expect(
        screen.getByText("25 Hz", { selector: ".instrument-panel__setting-now" })
      ).toBeInTheDocument();
      expect([...(choice as HTMLSelectElement).options].map((option) => option.value)).toEqual([
        "auto",
        "25",
        "12.5",
      ]);
    });

    it("sends the choice as that command, without a button of its own", async () => {
      const onCommand = withCompanions();
      await userEvent.selectOptions(screen.getByRole("combobox", { name: "Scan rate" }), "12.5");
      expect(onCommand).toHaveBeenCalledWith("scan_rate", { rate: "12.5" });
      expect(screen.queryByRole("button", { name: "Scan rate" })).not.toBeInTheDocument();
    });

    it("keeps the setting out of the deck", () => {
      withCompanions();
      expect(screen.getAllByRole("combobox", { name: "Scan rate" })).toHaveLength(1);
    });

    it("puts the command with no fields on the row of the key that applies the channels", () => {
      withCompanions();
      const apply = screen.getByRole("button", { name: "Apply" });
      const sampleKey = screen.getByRole("button", { name: "Sample" });
      expect(apply.parentElement).toBe(sampleKey.parentElement);
    });

    it("does the same when the command with no fields is the instrument's main action", () => {
      withCompanions(vi.fn(), "sample");
      expect(screen.getByRole("button", { name: "Apply" }).parentElement).toBe(
        screen.getByRole("button", { name: "Sample" }).parentElement
      );
    });

    it("still sends the sample as its own command", async () => {
      const onCommand = withCompanions();
      await userEvent.click(screen.getByRole("button", { name: "Sample" }));
      expect(onCommand).toHaveBeenCalledWith("sample", {});
    });

    it("leaves a command in the deck when nothing settles several things", () => {
      render(
        <InstrumentPanel instrument={instrument({ commands: [sample] })} onCommand={vi.fn()} />
      );
      expect(
        screen.getByRole("button", { name: "Sample" }).closest(".instrument-panel__channels")
      ).toBeNull();
    });
  });

  it("lines the readings up over the channels, one column to a channel", () => {
    const { container } = render(
      <InstrumentPanel
        instrument={instrument({
          commands: [configure],
          readouts: [
            { group: "A", key: "x", label: "X", precision: 1, role: "headline", unit: "V" },
            { group: "A", key: "y", label: "Y", precision: 1, role: "headline", unit: "V" },
          ],
          state: { x: 1, y: 2 },
        })}
        onCommand={vi.fn()}
      />
    );
    const readings = container.querySelector(".instrument-panel__readings--aligned") as HTMLElement;
    expect(readings).not.toBeNull();
    expect(readings.style.getPropertyValue("--columns")).toBe("2");
  });

  it("leaves the readings as they are where there is no table to line up with", () => {
    const { container } = render(
      <InstrumentPanel
        instrument={instrument({
          commands: [],
          readouts: [
            { group: "A", key: "x", label: "X", precision: 1, role: "headline", unit: "V" },
          ],
          state: { x: 1 },
        })}
        onCommand={vi.fn()}
      />
    );
    expect(container.querySelector(".instrument-panel__readings--aligned")).toBeNull();
  });

  it("takes the whole row of cards, so every channel is in view", () => {
    rowwise();
    expect(screen.getByRole("button", { name: "Apply" }).closest("form")).toHaveClass(
      "instrument-panel__command--rows"
    );
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
    await userEvent.click(screen.getByRole("button", { name: /^Apply/ }));

    expect(onCommand).toHaveBeenCalledWith("configure", {
      rows: {
        "1": { mode: "10v", label: "Rail 3V3" },
        "2": { mode: "tc_k", label: "Ambient" },
      },
    });
  });

  it("has nothing to apply until a control has been changed", () => {
    rowwise();
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("says how many changes are waiting to be applied, and sends none that were put back", async () => {
    const onCommand = rowwise();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "CH 2 Mode" }), "tc_k");
    await userEvent.type(screen.getByRole("textbox", { name: "CH 2 Label" }), "A");
    expect(screen.getByRole("button", { name: "Apply · 2 changes" })).toBeEnabled();
    await userEvent.clear(screen.getByRole("textbox", { name: "CH 2 Label" }));
    expect(screen.getByRole("button", { name: "Apply · 1 change" })).toBeEnabled();
    await userEvent.selectOptions(screen.getByRole("combobox", { name: "CH 2 Mode" }), "5v");
    expect(screen.getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(onCommand).not.toHaveBeenCalled();
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

describe("InstrumentPanel collapses an instrument the operator is not using", () => {
  beforeEach(() => localStorage.clear());

  it("hides the deck once collapsed, and shows it again once expanded", async () => {
    render(<InstrumentPanel instrument={instrument()} onCommand={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Set Level" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Collapse thing" }));
    expect(screen.queryByRole("button", { name: "Set Level" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Expand thing" }));
    expect(screen.getByRole("button", { name: "Set Level" })).toBeInTheDocument();
  });

  it("remembers a collapsed instrument across a reload", () => {
    render(
      <InstrumentPanel instrument={instrument({ instance_id: "thing0" })} onCommand={vi.fn()} />
    );
    localStorage.setItem("instrument-panel:collapsed:thing0", "1");

    render(
      <InstrumentPanel instrument={instrument({ instance_id: "thing0" })} onCommand={vi.fn()} />
    );
    expect(screen.getAllByRole("button", { name: "Expand thing" }).length).toBeGreaterThan(0);
  });
});

describe("InstrumentPanel offers a numeric field's declared control", () => {
  const address = {
    name: "address",
    label: "Address",
    type: "integer" as const,
    unit: "",
    min: 0,
    max: 127,
    choices: [],
    dial: false,
    choices_from: "known_addresses",
  };
  const command = { name: "read", label: "Read", fields: [address] };

  it("keeps a ranged field a plain entry when the provider opts it out of the dial", () => {
    render(
      <InstrumentPanel
        instrument={instrument({ commands: [command], primary_command: "read" })}
        onCommand={vi.fn()}
      />
    );
    expect(screen.queryByLabelText("Address")).toBeInTheDocument();
    expect(document.querySelector(".instrument-panel__dial")).toBeNull();
  });

  it("offers values the provider found at runtime as quick picks", async () => {
    const onCommand = vi.fn();
    render(
      <InstrumentPanel
        instrument={instrument({
          commands: [command],
          primary_command: "read",
          state: { known_addresses: [0x20, 0x50] },
        })}
        onCommand={onCommand}
      />
    );

    const pick = screen.getByRole("button", { name: "32" });
    await userEvent.click(pick);
    await userEvent.click(screen.getByRole("button", { name: "Read" }));

    expect(onCommand).toHaveBeenCalledWith("read", { address: 32 });
  });

  it("offers nothing to pick until the provider has found something", () => {
    render(
      <InstrumentPanel
        instrument={instrument({ commands: [command], primary_command: "read" })}
        onCommand={vi.fn()}
      />
    );
    expect(document.querySelector(".instrument-panel__picks")).toBeNull();
  });
});

describe("InstrumentPanel draws commands that share a group as one card", () => {
  const address = {
    name: "address",
    label: "Address",
    type: "integer" as const,
    unit: "",
    min: null,
    max: null,
    choices: [],
    format: "hex",
    choices_from: "found",
  };
  const register = {
    name: "register",
    label: "Register",
    type: "integer" as const,
    unit: "",
    min: null,
    max: null,
    choices: [],
  };
  const bus = instrument({
    commands: [
      { name: "write", label: "Write", group: "bus", fields: [address, register] },
      { name: "detect", label: "Detect", fields: [] },
      { name: "read", label: "Read", group: "bus", fields: [address] },
    ],
    state: { found: [0x10] },
  });

  it("enters a shared field once, with a key per command", () => {
    render(<InstrumentPanel instrument={bus} onCommand={vi.fn()} />);

    expect(screen.getAllByLabelText("Address")).toHaveLength(1);
    expect(screen.getByRole("button", { name: "Write" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Read" })).toBeInTheDocument();
  });

  it("sends the key's command with the fields it declares", async () => {
    const onCommand = vi.fn();
    render(<InstrumentPanel instrument={bus} onCommand={onCommand} />);

    await userEvent.click(screen.getByRole("button", { name: "10" }));
    await userEvent.type(screen.getByLabelText("Register"), "3");
    await userEvent.click(screen.getByRole("button", { name: "Read" }));

    expect(onCommand).toHaveBeenCalledWith("read", { address: 16 });
  });

  it("puts a command with no fields ahead of the card that uses what it finds", () => {
    render(<InstrumentPanel instrument={bus} onCommand={vi.fn()} />);

    const detect = screen.getByRole("button", { name: "Detect" });
    const write = screen.getByRole("button", { name: "Write" });
    expect(detect.compareDocumentPosition(write) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("draws a group of one as an ordinary command", () => {
    const lone = instrument({
      commands: [{ name: "read", label: "Read", group: "bus", fields: [register] }],
    });
    render(<InstrumentPanel instrument={lone} onCommand={vi.fn()} />);

    expect(document.querySelector(".instrument-panel__command--group")).toBeNull();
    expect(screen.getByRole("button", { name: "Read" })).toBeInTheDocument();
  });
});

describe("InstrumentPanel summarises a collapsed instrument", () => {
  it("carries the identifying details into the collapsed line", async () => {
    const meter = instrument({
      readouts: [
        { group: "Rail", key: "volts", label: "Volts", precision: 2, role: "headline", unit: "V" },
        {
          group: "Format",
          key: "width",
          label: "Width",
          precision: 0,
          role: "summary",
          unit: "px",
        },
        { group: "Format", key: "mode", label: "Mode", precision: null, role: "summary", unit: "" },
      ],
      state: { mode: "raw", volts: 3.3049, width: 3840 },
    });
    render(<InstrumentPanel instrument={meter} onCommand={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Collapse thing" }));

    // Only a group with nothing to burn large is identifying detail; the
    // rail's reading is what the expanded panel is for.
    expect(screen.getByText("Collapsed · Width 3840 px · Mode raw")).toBeInTheDocument();
  });

  it("says only that it is collapsed when nothing is declared", async () => {
    render(<InstrumentPanel instrument={instrument({ readouts: [] })} onCommand={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Collapse thing" }));

    expect(screen.getByText("Collapsed")).toBeInTheDocument();
  });

  it("still collapses and expands where storage is blocked", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    vi.spyOn(Storage.prototype, "removeItem").mockImplementation(() => {
      throw new Error("SecurityError");
    });
    render(<InstrumentPanel instrument={instrument()} onCommand={vi.fn()} />);

    await userEvent.click(screen.getByRole("button", { name: "Collapse thing" }));
    expect(screen.queryByRole("button", { name: "Set Level" })).toBeNull();

    await userEvent.click(screen.getByRole("button", { name: "Expand thing" }));
    expect(screen.getByRole("button", { name: "Set Level" })).toBeInTheDocument();
  });
});
