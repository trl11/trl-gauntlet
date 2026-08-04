import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import LogStream, { type LogLine } from "./LogStream";

const LINES: LogLine[] = [
  { level: "info", message: "starting the chamber", seq: 1, ts: 1767225600 },
  { level: "warning", message: "chamber is drifting", seq: 2, ts: 1767225601 },
  { level: "error", message: "rail voltage out of tolerance", seq: 3, ts: 1767225602 },
];

describe("LogStream", () => {
  it("renders every line and counts them", () => {
    render(<LogStream lines={LINES} />);
    expect(screen.getByText("starting the chamber")).toBeInTheDocument();
    expect(screen.getByText("3 of 3 lines")).toBeInTheDocument();
  });

  it("says so when there is nothing to show", () => {
    render(<LogStream lines={[]} />);
    expect(screen.getByText("No log lines yet.")).toBeInTheDocument();
  });

  it("filters by level", async () => {
    const { container } = render(<LogStream lines={LINES} />);
    const filterButton = container.querySelector(".fa-filter")!.closest("button")!;
    await userEvent.click(filterButton);
    await userEvent.selectOptions(screen.getByRole("combobox"), "error");
    expect(screen.queryByText("starting the chamber")).not.toBeInTheDocument();
    expect(screen.getByText("rail voltage out of tolerance")).toBeInTheDocument();
    expect(screen.getByText("1 of 3 lines")).toBeInTheDocument();
  });

  it("filters by text and highlights the match", async () => {
    const { container } = render(<LogStream lines={LINES} />);
    await userEvent.type(screen.getByLabelText("Find"), "chamber");
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(container.querySelectorAll("mark")).toHaveLength(2);
  });

  it("reports no matches for a search nothing satisfies", async () => {
    render(<LogStream lines={LINES} />);
    await userEvent.type(screen.getByLabelText("Find"), "nothing here");
    expect(screen.getByText("no matches")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next match" })).toBeDisabled();
  });

  it("toggles line wrapping", async () => {
    const { container } = render(<LogStream lines={LINES} />);
    expect(container.querySelector(".log-stream__view--wrap")).toBeNull();
    await userEvent.click(screen.getByLabelText("Wrap lines"));
    expect(container.querySelector(".log-stream__view--wrap")).not.toBeNull();
  });

  it("renders only a window of a very long stream", () => {
    const many: LogLine[] = Array.from({ length: 20000 }, (_, index) => ({
      level: "info",
      message: `line ${index}`,
      seq: index,
      ts: null,
    }));
    const { container } = render(<LogStream lines={many} />);
    expect(container.querySelectorAll(".log-stream__row").length).toBeLessThan(200);
    expect(screen.getByText("20000 of 20000 lines")).toBeInTheDocument();
  });
});
