import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { Note } from "@api/types";

import NotesPanel from "./NotesPanel";

function note(partial: Partial<Note> & { id: number }): Note {
  return {
    author: "gabe",
    body: "reflowed U7",
    created_at: "2026-01-02T00:00:00Z",
    ...partial,
  };
}

function renderPanel(props: Partial<React.ComponentProps<typeof NotesPanel>> = {}) {
  return render(<NotesPanel notes={[]} onAdd={vi.fn()} onDelete={vi.fn()} {...props} />);
}

describe("NotesPanel", () => {
  it("says so when nothing has been written yet", () => {
    renderPanel();
    expect(screen.getByText("No notes yet.")).toBeInTheDocument();
  });

  it("lists each note with who wrote it", () => {
    renderPanel({
      notes: [note({ id: 1 }), note({ id: 2, author: null, body: "swapped harness" })],
    });
    expect(screen.getByText("reflowed U7")).toBeInTheDocument();
    expect(screen.getByText("swapped harness")).toBeInTheDocument();
    expect(screen.getByText("gabe")).toBeInTheDocument();
    expect(screen.getByText("anonymous")).toBeInTheDocument();
  });

  it("heads itself by default, and holds its peace when the caller heads it", () => {
    const { unmount } = renderPanel();
    expect(screen.getByRole("heading", { name: "Notes" })).toBeInTheDocument();
    unmount();

    renderPanel({ titled: false });
    expect(screen.queryByRole("heading", { name: "Notes" })).not.toBeInTheDocument();
  });

  it("names its own region only while it heads itself", () => {
    const { unmount } = renderPanel();
    expect(screen.getByRole("region", { name: "Notes" })).toBeInTheDocument();
    unmount();

    renderPanel({ titled: false });
    expect(screen.queryByRole("region", { name: "Notes" })).not.toBeInTheDocument();
  });
});

describe("NotesPanel composing", () => {
  it("will not post an empty note", async () => {
    const onAdd = vi.fn();
    renderPanel({ onAdd });
    expect(screen.getByRole("button", { name: "Add note" })).toBeDisabled();
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("will not post one that is only whitespace", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    renderPanel({ onAdd });
    await user.type(screen.getByLabelText("Add a note"), "   ");
    expect(screen.getByRole("button", { name: "Add note" })).toBeDisabled();
  });

  it("posts the note and the author, trimmed", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    renderPanel({ onAdd });

    await user.type(screen.getByLabelText("Add a note"), "  cracked lid  ");
    await user.type(screen.getByLabelText("Author"), " gabe ");
    await user.click(screen.getByRole("button", { name: "Add note" }));

    expect(onAdd).toHaveBeenCalledWith("cracked lid", "gabe");
  });

  it("posts no author when the field is left empty", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    renderPanel({ onAdd });

    await user.type(screen.getByLabelText("Add a note"), "cracked lid");
    await user.click(screen.getByRole("button", { name: "Add note" }));

    expect(onAdd).toHaveBeenCalledWith("cracked lid", null);
  });

  it("clears the body once the note is accepted, keeping the author", async () => {
    const user = userEvent.setup();
    renderPanel({ onAdd: vi.fn().mockResolvedValue(undefined) });

    await user.type(screen.getByLabelText("Add a note"), "cracked lid");
    await user.type(screen.getByLabelText("Author"), "gabe");
    await user.click(screen.getByRole("button", { name: "Add note" }));

    await waitFor(() => expect(screen.getByLabelText("Add a note")).toHaveValue(""));
    expect(screen.getByLabelText("Author")).toHaveValue("gabe");
  });

  it("posts nothing while a mutation is already in flight", async () => {
    const user = userEvent.setup();
    const onAdd = vi.fn();
    renderPanel({ busy: true, notes: [note({ id: 1 })], onAdd });

    expect(screen.getByLabelText("Add a note")).toBeDisabled();
    await user.click(screen.getByRole("button", { name: "Add note" }));
    expect(onAdd).not.toHaveBeenCalled();
  });
});

describe("NotesPanel deleting", () => {
  it("deletes a note once the operator confirms", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    renderPanel({ notes: [note({ id: 7 })], onDelete });

    await user.click(screen.getByRole("button", { name: "Delete note 7" }));
    await user.click(screen.getByRole("button", { name: "Confirm" }));

    expect(onDelete).toHaveBeenCalledWith(7);
  });

  it("leaves the note alone when the confirmation is dismissed", async () => {
    const user = userEvent.setup();
    const onDelete = vi.fn();
    renderPanel({ notes: [note({ id: 7 })], onDelete });

    await user.click(screen.getByRole("button", { name: "Delete note 7" }));
    await user.click(screen.getByRole("button", { name: "Dismiss" }));

    expect(onDelete).not.toHaveBeenCalled();
  });

  it("cannot delete while a mutation is in flight", () => {
    renderPanel({ busy: true, notes: [note({ id: 7 })] });
    expect(screen.getByRole("button", { name: "Delete note 7" })).toBeDisabled();
  });

  it("spins while a mutation is in flight", () => {
    const { container } = renderPanel({ busy: true });
    expect(container.querySelector(".notes-panel__spinner")).not.toBeNull();
  });

  it("spins without its heading too, so a headed caller still sees it", () => {
    const { container } = renderPanel({ busy: true, titled: false });
    expect(container.querySelector(".notes-panel__spinner")).not.toBeNull();
  });

  it("takes a class from the caller, so a page can place it", () => {
    const { container } = renderPanel({ className: "unit-detail__notes" });
    expect(container.querySelector(".notes-panel.unit-detail__notes")).not.toBeNull();
  });

  it("shows when each note was written", () => {
    renderPanel({ notes: [note({ id: 1 })] });
    const time = screen.getByText(/ago|just now/i);
    expect(within(time.closest("li") as HTMLElement).getByText("reflowed U7")).toBeInTheDocument();
  });
});
