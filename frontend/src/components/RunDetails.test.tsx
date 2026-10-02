import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, getRunVerdict } from "@api/client";
import type { RunRow } from "@api/types";

import RunDetails from "./RunDetails";
import { pending } from "../test/queries";

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return { ...actual, getRunVerdict: vi.fn() };
});

const RUN: RunRow = {
  duration_s: 12,
  ended_at: "2026-01-01T00:00:12Z",
  fail_reason: null,
  location: "Bench 3",
  operator: "Ada",
  profile: "smoke.yaml",
  run_dir: "/runs/r1",
  run_id: "r1",
  session: null,
  started_at: "2026-01-01T00:00:00Z",
  status: "failed",
  suite: "ssd",
  target: null,
  unit_serial: "SN-1",
  verdict: "FAIL",
};

function renderDetails(run: RunRow = RUN) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter>
        <RunDetails run={run} />
      </MemoryRouter>
    </QueryClientProvider>
  );
}

/** What the list gives beside one term. */
function valueOf(term: string): string | null {
  return screen.getByText(term).nextElementSibling?.textContent ?? null;
}

beforeEach(() => {
  vi.mocked(getRunVerdict).mockResolvedValue({
    abort_reason: "",
    aborted: false,
    duration_s: 12,
    ended_at_utc: "2026-01-01T00:00:12Z",
    failures: 1,
    passed: false,
    reason: "rail low on cycle 3",
    results: [],
    started_at_utc: "2026-01-01T00:00:00Z",
    stopped_early: false,
    successes: 2,
    tests: [],
    total_iterations: 3,
  });
});

describe("RunDetails", () => {
  it("links the run and counts its tests from the verdict", async () => {
    renderDetails();

    expect(screen.getByRole("link", { name: "r1" })).toHaveAttribute("href", "/runs/r1");
    expect(await screen.findByText("2 passed, 1 failed, 3 iterations")).toBeInTheDocument();
    expect(valueOf("Verdict")).toBe("FAIL");
    expect(valueOf("Reason")).toBe("rail low on cycle 3");
  });

  it("prefers the reason recorded on the run over the verdict's", async () => {
    renderDetails({ ...RUN, fail_reason: "suite crashed" });

    await screen.findByText("2 passed, 1 failed, 3 iterations");
    expect(valueOf("Reason")).toBe("suite crashed");
  });

  it("says the counts are loading while the verdict is read", () => {
    vi.mocked(getRunVerdict).mockReturnValue(pending());
    renderDetails();

    expect(valueOf("Tests")).toBe("loading");
  });

  it("says so when the run recorded no verdict, with dashes for what is missing", async () => {
    vi.mocked(getRunVerdict).mockRejectedValue(new ApiError(404, "no verdict.json", "/api"));
    renderDetails({ ...RUN, verdict: null });

    expect(await screen.findByText("no verdict recorded")).toBeInTheDocument();
    expect(valueOf("Verdict")).toBe("-");
    expect(valueOf("Reason")).toBe("-");
    expect(valueOf("Target")).toBe("-");
    expect(valueOf("Session")).toBe("-");
    expect(valueOf("Operator")).toBe("Ada");
    expect(valueOf("Artifacts")).toBe("/runs/r1");
  });
});
