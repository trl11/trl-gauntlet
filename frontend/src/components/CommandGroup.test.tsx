import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { InstrumentCommand, InstrumentField } from "@api/types";

import CommandGroup from "./CommandGroup";

function makeField(overrides: Partial<InstrumentField>): InstrumentField {
  return {
    choices: [],
    label: "",
    max: null,
    min: null,
    name: "value",
    type: "string",
    unit: "",
    ...overrides,
  };
}

const address = makeField({
  choices_from: "found",
  format: "hex",
  label: "Address",
  name: "address",
  type: "integer",
});
const register = makeField({ label: "Register", name: "register" });
const data = makeField({ label: "Data", name: "data" });

const write: InstrumentCommand = {
  danger: true,
  fields: [address, register, data],
  group: "bus",
  label: "Write",
  name: "write",
};
const read: InstrumentCommand = {
  fields: [address, register],
  group: "bus",
  label: "",
  name: "read",
};

describe("CommandGroup", () => {
  it("draws each field shared across the group once", () => {
    render(<CommandGroup commands={[write, read]} disabled={false} onSubmit={vi.fn()} />);

    expect(screen.getAllByLabelText("Address")).toHaveLength(1);
    expect(screen.getAllByLabelText("Register")).toHaveLength(1);
    expect(screen.getByLabelText("Data")).toBeInTheDocument();
  });

  it("gives each command a key, named by its label or else its name", () => {
    render(<CommandGroup commands={[write, read]} disabled={false} onSubmit={vi.fn()} />);

    expect(screen.getByRole("button", { name: "Write" })).toHaveClass(
      "instrument-panel__go--danger"
    );
    expect(screen.getByRole("button", { name: "read" })).not.toHaveClass(
      "instrument-panel__go--danger"
    );
  });

  it("sends only the fields a command declares, read from the shared controls", async () => {
    const onSubmit = vi.fn();
    render(<CommandGroup commands={[write, read]} disabled={false} onSubmit={onSubmit} />);

    await userEvent.type(screen.getByLabelText("Address"), "4a");
    await userEvent.type(screen.getByLabelText("Register"), "ctrl");
    await userEvent.type(screen.getByLabelText("Data"), "on");

    await userEvent.click(screen.getByRole("button", { name: "read" }));
    expect(onSubmit).toHaveBeenLastCalledWith(read, { address: 74, register: "ctrl" });

    await userEvent.click(screen.getByRole("button", { name: "Write" }));
    expect(onSubmit).toHaveBeenLastCalledWith(write, {
      address: 74,
      data: "on",
      register: "ctrl",
    });
  });

  it("offers what a detect found once, below the keys, and a pick fills the field", async () => {
    const onSubmit = vi.fn();
    render(
      <CommandGroup
        commands={[write, read]}
        disabled={false}
        onSubmit={onSubmit}
        state={{ found: [16, 72] }}
      />
    );

    const picks = screen.getByRole("group", { name: "Detected Address" });
    await userEvent.click(within(picks).getByRole("button", { name: "48" }));

    expect(screen.getByLabelText("Address")).toHaveValue("48");
    expect(within(picks).getByRole("button", { name: "48" })).toHaveClass(
      "instrument-panel__pick--selected"
    );
    await userEvent.click(screen.getByRole("button", { name: "read" }));
    expect(onSubmit).toHaveBeenCalledWith(read, { address: 72, register: "" });
  });

  it("says nothing has been detected before a detect has run", () => {
    render(<CommandGroup commands={[write, read]} disabled={false} onSubmit={vi.fn()} />);

    expect(screen.getByText("Detected Address")).toBeInTheDocument();
    expect(screen.getByText("nothing detected yet")).toBeInTheDocument();
  });

  it("draws no detected row for a group with no field that names a source", () => {
    const plain: InstrumentCommand = { fields: [register], label: "Poke", name: "poke" };
    render(<CommandGroup commands={[plain]} disabled={false} onSubmit={vi.fn()} />);

    expect(screen.queryByText("nothing detected yet")).not.toBeInTheDocument();
  });

  it("disables every control and key while the instrument is busy", () => {
    render(
      <CommandGroup commands={[write, read]} disabled onSubmit={vi.fn()} state={{ found: [16] }} />
    );

    expect(screen.getByLabelText("Address")).toBeDisabled();
    expect(screen.getByRole("button", { name: "Write" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "10" })).toBeDisabled();
  });
});
