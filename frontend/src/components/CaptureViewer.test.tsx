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

  it("stands down when the capture cannot be read", async () => {
    getArtifactText.mockResolvedValue("t_s,ch0");
    renderViewer();
    expect(await screen.findByText("No samples")).toBeInTheDocument();
  });

  it("switches a channel back on", async () => {
    renderViewer();
    await screen.findByText(/4 samples/);
    const channel = screen.getByRole("button", { name: "ch1" });

    await userEvent.click(channel);
    await userEvent.click(channel);

    expect(channel).toHaveAttribute("aria-pressed", "true");
  });

  it("names a capture whose file carries no iteration by its path", async () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CaptureViewer paths={["captures/scope.csv"]} runId="RUN-0001" />
      </QueryClientProvider>
    );

    expect(
      await screen.findByRole("option", { name: "Iteration captures/scope.csv" })
    ).toBeInTheDocument();
  });

  it("gives no rate for a capture of a single sample", async () => {
    getArtifactText.mockResolvedValue("t_s,ch0\n0,0.0025");
    renderViewer();

    expect(await screen.findByText(/1 samples at 0 kS\/s, all shown/)).toBeInTheDocument();
  });

  it("draws a long capture as an envelope and says so", async () => {
    const rows = Array.from({ length: 2000 }, (_, index) => `${index * 4e-5},${index % 7}`);
    getArtifactText.mockResolvedValue(["t_s,ch0", ...rows].join("\n"));
    renderViewer();

    expect(
      await screen.findByText(/2,000 samples .* all shown, as an envelope — zoom in/)
    ).toBeInTheDocument();
  });

  it("says why a capture that cannot be fetched is not drawn", async () => {
    getArtifactText.mockRejectedValue(new Error("gone"));
    renderViewer();

    expect(await screen.findByText("No samples")).toBeInTheDocument();
    expect(
      screen.getByText("Nothing could be read from captures/capture_0001.csv.")
    ).toBeInTheDocument();
  });

  it("offers nothing to read when the run holds no captures", () => {
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <CaptureViewer paths={[]} runId="RUN-0001" />
      </QueryClientProvider>
    );

    expect(screen.getByText("Nothing could be read from this run's captures.")).toBeInTheDocument();
    expect(getArtifactText).not.toHaveBeenCalled();
  });

  describe("once laid out", () => {
    beforeEach(() => {
      // jsdom lays nothing out, and the chart draws nothing into no space.
      vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockReturnValue(
        new DOMRect(0, 0, 800, 360)
      );
    });

    it("zooms to the window picked on the brush, and resets back to the whole capture", async () => {
      renderViewer();
      await screen.findByText(/4 samples/);
      const reset = screen.getByRole("button", { name: "Reset zoom" });
      expect(reset).toBeDisabled();

      const [start] = await screen.findAllByRole("slider");
      start.focus();
      await userEvent.keyboard("{ArrowRight}");

      expect(screen.getByText(/showing 3 of them/)).toBeInTheDocument();
      expect(reset).toBeEnabled();

      await userEvent.click(reset);

      expect(screen.getByText(/all shown/)).toBeInTheDocument();
      expect(reset).toBeDisabled();
    });

    it("reads the time axis in milliseconds", async () => {
      renderViewer();
      await screen.findByText(/4 samples/);

      expect(await screen.findAllByText(/^[\d.]+ms$/)).not.toHaveLength(0);
    });
  });
});
