import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { getArtifactText, listArtifacts } from "@api/client";
import ArtifactList, { type ArtifactGallery } from "./ArtifactList";

vi.mock("@api/client", () => ({
  artifactUrl: (runId: string, path: string) => `/api/runs/${runId}/artifacts/${path}`,
  getArtifactText: vi.fn(),
  listArtifacts: vi.fn(),
}));

const listed = vi.mocked(listArtifacts);
const text = vi.mocked(getArtifactText);

function renderList(galleries?: ArtifactGallery[]) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ArtifactList runId="run-1" galleries={galleries} />
    </QueryClientProvider>
  );
}

describe("ArtifactList", () => {
  beforeEach(() => {
    listed.mockResolvedValue({
      run_id: "run-1",
      run_dir: "/runs/run-1",
      artifacts: [
        { path: "verdict.json", size: 120, text: true },
        { path: "frames/001.png", size: 4096, text: false },
      ],
    });
    text.mockResolvedValue('{"passed": true}');
  });

  it("lists every file with its size and type", async () => {
    renderList();
    expect(await screen.findByText("verdict.json")).toBeInTheDocument();
    expect(screen.getByText("120 B")).toBeInTheDocument();
    expect(screen.getByText("4.0 KB")).toBeInTheDocument();
    expect(screen.getByText("png")).toBeInTheDocument();
  });

  it("offers a download link for every file", async () => {
    renderList();
    const link = await screen.findByRole("link", { name: "Download frames/001.png" });
    expect(link).toHaveAttribute("href", "/api/runs/run-1/artifacts/frames/001.png");
  });

  it("previews a text artifact, pretty-printing JSON", async () => {
    renderList();
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/"passed": true/)).toBeInTheDocument();
  });

  it("offers no preview for a binary artifact", async () => {
    renderList();
    await screen.findByText("frames/001.png");
    expect(screen.getAllByRole("button", { name: "Preview" })).toHaveLength(1);
  });

  it("offers a download instead of an inline preview for a large text file", async () => {
    listed.mockResolvedValue({
      run_id: "run-1",
      run_dir: "/runs/run-1",
      artifacts: [{ path: "metrics.jsonl", size: 600_000, text: true }],
    });
    renderList();
    await userEvent.click(await screen.findByRole("button", { name: "Preview" }));
    expect(await screen.findByText(/too large to preview/)).toBeInTheDocument();
    expect(text).not.toHaveBeenCalled();
  });

  it("says so when the run wrote nothing", async () => {
    listed.mockResolvedValue({ run_id: "run-1", run_dir: "/runs/run-1", artifacts: [] });
    renderList();
    expect(await screen.findByText("No artifacts")).toBeInTheDocument();
  });

  it("folds a gallery's images into one row so they cannot bury the rest", async () => {
    renderList([{ paths: ["frames/001.png"], tab: "snapshots" }]);
    expect(await screen.findByText("verdict.json")).toBeInTheDocument();
    expect(screen.queryByText("frames/001.png")).not.toBeInTheDocument();
    expect(screen.getByText("frames/")).toBeInTheDocument();
    expect(screen.getByText(/1 in the snapshots tab/)).toBeInTheDocument();
  });

  it("gives each gallery its own row, so one tab's files are not counted as another's", async () => {
    listed.mockResolvedValue({
      run_id: "run-1",
      run_dir: "/runs/run-1",
      artifacts: [
        { path: "verdict.json", size: 120, text: true },
        { path: "frames/001.png", size: 4096, text: false },
        { path: "traces/001.png", size: 2048, text: false },
      ],
    });
    renderList([
      { paths: ["frames/001.png"], tab: "snapshots" },
      { paths: ["traces/001.png"], tab: "traces" },
    ]);
    expect(await screen.findByText("frames/")).toBeInTheDocument();
    expect(screen.getByText("traces/")).toBeInTheDocument();
    expect(screen.queryByText("traces/001.png")).not.toBeInTheDocument();
    expect(screen.getByText(/1 in the traces tab/)).toBeInTheDocument();
  });

  it("reports a failure to list the directory", async () => {
    listed.mockRejectedValue(new Error("run directory missing"));
    renderList();
    expect(await screen.findByRole("alert")).toHaveTextContent("run directory missing");
  });
});
