import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import CaptureViewer from "./CaptureViewer";

const getArtifactText = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    getArtifactText: (...args: unknown[]) => getArtifactText(...args),
  };
});

const PATHS = ["captures/capture_0001.csv", "captures/capture_0002.csv"];

const CSV = [
  "t_s,ch0,ch1",
  "0,0.0025,0.5",
  "4e-05,0.0026,0.51",
  "8e-05,0.0024,0.52",
  "0.00012,0.0027,0.53",
].join("\n");

function renderViewer() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CaptureViewer paths={PATHS} runId="RUN-0001" />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getArtifactText.mockResolvedValue(CSV);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("CaptureViewer", () => {
  it("reads the first capture and says what it holds", async () => {
    renderViewer();
    expect(await screen.findByText(/4 samples at 25 kS\/s/)).toBeInTheDocument();
    expect(getArtifactText).toHaveBeenCalledWith("RUN-0001", "captures/capture_0001.csv");
  });

  it("offers every capture by the iteration that took it", async () => {
    renderViewer();
    await screen.findByText(/4 samples/);
    expect(screen.getByRole("option", { name: "Iteration 1" })).toBeInTheDocument();
    expect(screen.getByRole("option", { name: "Iteration 2" })).toBeInTheDocument();
  });

  it("reads another capture when one is picked", async () => {
    renderViewer();
    await screen.findByText(/4 samples/);
    await userEvent.selectOptions(screen.getByLabelText("Capture"), PATHS[1]);
    expect(getArtifactText).toHaveBeenCalledWith("RUN-0001", "captures/capture_0002.csv");
  });

  it("names every channel the file holds, from the file", async () => {
    renderViewer();
    await screen.findByText(/4 samples/);
    expect(screen.getByRole("button", { name: "ch0" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("button", { name: "ch1" })).toHaveAttribute("aria-pressed", "true");
  });

  it("switches a channel off without losing it", async () => {
    renderViewer();
    await screen.findByText(/4 samples/);
    await userEvent.click(screen.getByRole("button", { name: "ch1" }));
    expect(screen.getByRole("button", { name: "ch1" })).toHaveAttribute("aria-pressed", "false");
  });

  it("fits the vertical window to the channels on show", async () => {
    renderViewer();
    await screen.findByText(/4 samples/);
    await userEvent.click(screen.getByRole("button", { name: "ch1" }));
    await userEvent.click(screen.getByRole("button", { name: "Fit" }));
    expect(screen.getByLabelText("Y axis min")).toHaveValue(0.0024);
    expect(screen.getByLabelText("Y axis max")).toHaveValue(0.0027);
  });

  it("stands down when the capture cannot be read", async () => {
    getArtifactText.mockResolvedValue("t_s,ch0");
    renderViewer();
    expect(await screen.findByText("No samples")).toBeInTheDocument();
  });
});
