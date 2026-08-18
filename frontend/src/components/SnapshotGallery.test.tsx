import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import SnapshotGallery, { type Snapshot } from "./SnapshotGallery";

vi.mock("@api/client", () => ({
  artifactUrl: (runId: string, path: string) => `/api/runs/${runId}/artifacts/${path}`,
}));

const snapshots: Snapshot[] = [
  { iteration: 1, path: "frames/snapshot_0001.png" },
  { iteration: 2, path: "frames/snapshot_0002.png" },
  { iteration: 3, path: "frames/snapshot_0003.png" },
];

function renderGallery(rows: Snapshot[] = snapshots) {
  return render(<SnapshotGallery runId="run-1" snapshots={rows} />);
}

describe("SnapshotGallery", () => {
  it("draws every snapshot, pointing each at its artifact", () => {
    renderGallery();
    const images = screen.getAllByRole("img");
    expect(images).toHaveLength(3);
    expect(images[0]).toHaveAttribute("src", "/api/runs/run-1/artifacts/frames/snapshot_0001.png");
    expect(images[2]).toHaveAttribute("src", "/api/runs/run-1/artifacts/frames/snapshot_0003.png");
  });

  it("names each thumbnail by the iteration it came from", () => {
    renderGallery();
    expect(screen.getByText("#1")).toBeInTheDocument();
    expect(screen.getByText("#3")).toBeInTheDocument();
  });

  it("falls back to the file name for an image recorded against no iteration", () => {
    renderGallery([{ iteration: null, path: "frames/loose.png" }]);
    expect(screen.getByText("loose.png")).toBeInTheDocument();
  });

  it("offers every snapshot as a download", () => {
    renderGallery();
    const link = screen.getByLabelText("Download snapshot_0002.png");
    expect(link).toHaveAttribute("href", "/api/runs/run-1/artifacts/frames/snapshot_0002.png");
    expect(link).toHaveAttribute("download");
  });

  it("opens one full size, and steps to the next", async () => {
    const user = userEvent.setup();
    renderGallery();

    await user.click(screen.getByLabelText("Open snapshot_0001.png"));
    expect(screen.getByText("1 of 3 — iteration 1")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Next snapshot" }));
    expect(screen.getByText("2 of 3 — iteration 2")).toBeInTheDocument();
  });

  it("stops at both ends rather than wrapping", async () => {
    const user = userEvent.setup();
    renderGallery();

    await user.click(screen.getByLabelText("Open snapshot_0001.png"));
    expect(screen.getByRole("button", { name: "Previous snapshot" })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: "Next snapshot" }));
    await user.click(screen.getByRole("button", { name: "Next snapshot" }));
    expect(screen.getByText("3 of 3 — iteration 3")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Next snapshot" })).toBeDisabled();
  });

  it("says so when the run recorded no images", () => {
    renderGallery([]);
    expect(screen.getByText("No snapshots")).toBeInTheDocument();
    expect(screen.queryAllByRole("img")).toHaveLength(0);
  });
});
