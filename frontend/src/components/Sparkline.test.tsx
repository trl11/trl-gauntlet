import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Sparkline from "./Sparkline";

function line(container: HTMLElement): SVGPolylineElement | null {
  return container.querySelector("polyline");
}

describe("Sparkline", () => {
  it("draws nothing until there are two samples to join", () => {
    const { container } = render(<Sparkline values={[4]} />);

    expect(line(container)).toBeNull();
  });

  it("spreads the samples across the width, oldest first", () => {
    const { container } = render(<Sparkline values={[0, 5, 10]} />);

    expect(line(container)?.getAttribute("points")).toBe("0.0,16.5 32.0,9.0 64.0,1.5");
  });

  it("scales to its own range rather than to an absolute one", () => {
    const { container } = render(<Sparkline values={[100, 101]} />);

    expect(line(container)?.getAttribute("points")).toBe("0.0,16.5 64.0,1.5");
  });

  it("draws a reading that has not moved along the middle", () => {
    const { container } = render(<Sparkline values={[7, 7, 7]} />);

    expect(line(container)?.getAttribute("points")).toBe("0.0,9.0 32.0,9.0 64.0,9.0");
  });
});
