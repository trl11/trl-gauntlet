import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Shortcut } from "@hooks/useGlobalShortcuts";

import ShortcutsHelp from "./ShortcutsHelp";

const SHORTCUTS: Shortcut[] = [
  { keys: "?", description: "Toggle this help" },
  { keys: "g d", description: "Go to Dashboard" },
];

describe("ShortcutsHelp", () => {
  it("lists every shortcut it is given, in order", () => {
    render(<ShortcutsHelp shortcuts={SHORTCUTS} onClose={vi.fn()} />);

    const rows = screen.getAllByRole("term");
    expect(rows.map((row) => row.textContent)).toEqual(["?", "g d"]);
    expect(screen.getByText("Go to Dashboard")).toBeInTheDocument();
  });

  it("pairs each key with its description", () => {
    render(<ShortcutsHelp shortcuts={SHORTCUTS} onClose={vi.fn()} />);

    const row = screen.getByText("g d").closest(".shortcuts-help__row");
    expect(within(row as HTMLElement).getByText("Go to Dashboard")).toBeInTheDocument();
  });

  it("is titled so the overlay is identifiable", () => {
    render(<ShortcutsHelp shortcuts={SHORTCUTS} onClose={vi.fn()} />);

    expect(screen.getByRole("heading", { name: "Keyboard shortcuts" })).toBeInTheDocument();
  });

  it("closes on request", async () => {
    const onClose = vi.fn();
    render(<ShortcutsHelp shortcuts={SHORTCUTS} onClose={onClose} />);

    // The kit's modal renders one icon-only button in its header: close.
    await userEvent.click(screen.getByRole("button"));

    expect(onClose).toHaveBeenCalled();
  });

  it("renders nothing but the heading when there are no shortcuts", () => {
    render(<ShortcutsHelp shortcuts={[]} onClose={vi.fn()} />);

    expect(screen.queryAllByRole("term")).toEqual([]);
  });
});
