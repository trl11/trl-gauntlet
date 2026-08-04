import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import RowMenu from "./RowMenu";

describe("RowMenu", () => {
  it("opens to reveal its items, hidden until then", async () => {
    render(
      <RowMenu ariaLabel="Actions for run r1" items={[{ label: "Delete", onSelect: vi.fn() }]} />
    );
    expect(screen.queryByText("Delete")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Actions for run r1" }));
    expect(screen.getByText("Delete")).toBeInTheDocument();
  });

  it("closes and calls onSelect when an item is picked", async () => {
    const onSelect = vi.fn();
    render(<RowMenu ariaLabel="Actions for run r1" items={[{ label: "Delete", onSelect }]} />);
    await userEvent.click(screen.getByRole("button", { name: "Actions for run r1" }));

    const menu = document.querySelector(".row-menu") as HTMLElement;
    await userEvent.click(within(menu).getByText("Delete"));

    expect(onSelect).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("Delete")).not.toBeInTheDocument();
  });

  it("marks a danger item so the stylesheet can colour it", async () => {
    render(
      <RowMenu
        ariaLabel="Actions for run r1"
        items={[{ danger: true, label: "Delete", onSelect: vi.fn() }]}
      />
    );
    await userEvent.click(screen.getByRole("button", { name: "Actions for run r1" }));
    expect(screen.getByText("Delete")).toHaveClass("row-menu__item--danger");
  });
});
