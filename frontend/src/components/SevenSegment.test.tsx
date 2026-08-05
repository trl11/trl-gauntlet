import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import SevenSegment from "./SevenSegment";

describe("SevenSegment", () => {
  it("lights one cell per character and hangs the point on its digit", () => {
    const { container } = render(<SevenSegment tone="green" value="28.00" />);

    expect(container.querySelectorAll(".seven-segment__cell")).toHaveLength(4);
    expect(container.querySelectorAll("circle")).toHaveLength(1);
  });

  it("keeps the reading readable to assistive technology", () => {
    render(<SevenSegment tone="red" value="3.000" />);

    expect(screen.getByText("3.000")).toBeInTheDocument();
  });

  it("spells a word the bars can make", () => {
    const { container } = render(<SevenSegment tone="amber" value="off" />);

    expect(container.querySelectorAll(".seven-segment__cell")).toHaveLength(3);
  });

  it("falls back to plain text for a reading the bars cannot spell", () => {
    const { container } = render(<SevenSegment tone="amber" value="warming" />);

    expect(container.querySelectorAll(".seven-segment__cell")).toHaveLength(0);
    expect(screen.getByText("warming")).toBeInTheDocument();
  });
});
