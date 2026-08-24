import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import FieldInput from "./FieldInput";

/**
 * The kit paints a field's message from its own blur and invalid listeners
 * only, so an error the app supplies while someone types would otherwise stay
 * invisible until they left the field. These cover the wrapper that fixes it;
 * swapping it back for the kit's `Input` fails them.
 */
function Harness({ error }: { error?: string }) {
  const [value, setValue] = useState("");
  return (
    <FieldInput
      id="serial"
      label="Serial"
      error={error}
      value={value}
      onChange={(event) => setValue(event.target.value)}
    />
  );
}

describe("FieldInput", () => {
  it("shows an error the caller supplies, with the field never focused", () => {
    render(<Harness error="That is not a valid serial." />);

    expect(screen.getByText("That is not a valid serial.")).toBeInTheDocument();
  });

  it("shows an error that appears while the field still has focus", async () => {
    const user = userEvent.setup();
    const { rerender } = render(<Harness />);
    await user.click(screen.getByLabelText("Serial"));
    expect(screen.queryByText("Too long.")).not.toBeInTheDocument();

    rerender(<Harness error="Too long." />);

    expect(await screen.findByText("Too long.")).toBeInTheDocument();
  });

  it("clears the message once the error goes, without waiting for a blur", async () => {
    const { rerender } = render(<Harness error="Too long." />);
    expect(screen.getByText("Too long.")).toBeInTheDocument();

    rerender(<Harness />);

    expect(screen.queryByText("Too long.")).not.toBeInTheDocument();
  });
});
