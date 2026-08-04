import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Markdown from "./Markdown";

describe("Markdown", () => {
  it("renders a heading as a heading, not literal source", () => {
    render(<Markdown text="# system_stats — PASS" />);
    expect(screen.getByRole("heading", { name: "system_stats — PASS" })).toBeInTheDocument();
    expect(screen.queryByText("# system_stats — PASS")).not.toBeInTheDocument();
  });

  it("renders a pipe table as a real table", () => {
    render(
      <Markdown
        text={["| Field | Value |", "|---|---|", "| Run id | run-1 |", "| Duration | 1.0m |"].join(
          "\n"
        )}
      />
    );
    const table = screen.getByRole("table");
    expect(table).toBeInTheDocument();
    expect(screen.getByRole("columnheader", { name: "Field" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "run-1" })).toBeInTheDocument();
    expect(screen.getByRole("cell", { name: "1.0m" })).toBeInTheDocument();
  });

  it("renders bold and code spans inline", () => {
    render(<Markdown text="This suite passed with **60 iterations** using `system_stats`." />);
    expect(screen.getByText("60 iterations")).toBeInTheDocument();
    expect(screen.getByText("system_stats")).toBeInTheDocument();
  });

  it("renders plain prose as a paragraph", () => {
    render(<Markdown text="Everything ran within thermal limits." />);
    expect(screen.getByText("Everything ran within thermal limits.")).toBeInTheDocument();
  });
});
