import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { Instrument } from "@api/types";

import InstrumentsPage from "./InstrumentsPage";
import { pending, spinners } from "../test/queries";

const listInstruments = vi.fn();
const rescanInstruments = vi.fn();
const sendInstrumentCommand = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    listInstruments: () => listInstruments(),
    rescanInstruments: () => rescanInstruments(),
    sendInstrumentCommand: (...args: unknown[]) => sendInstrumentCommand(...args),
  };
});

function instrument(overrides: Partial<Instrument>): Instrument {
  return {
    available: true,
    commands: [],
    description: "",
    instance_id: "x0",
    kind: "thing",
    name: "thing",
    unavailable_reason: "",
    state: {},
    ...overrides,
  };
}

const psu = instrument({
  commands: [
    {
      name: "set_output",
      label: "Set Output",
      fields: [
        {
          name: "channel",
          label: "Channel",
          type: "string",
          unit: "",
          min: null,
          max: null,
          choices: ["1"],
        },
        {
          name: "enabled",
          label: "Enabled",
          type: "boolean",
          unit: "",
          min: null,
          max: null,
          choices: [],
        },
      ],
    },
  ],
  description: "Two-channel bench supply.",
  instance_id: "psu0",
  kind: "psu",
  name: "psu",
  state: { channels: { "1": { voltage: 12 } } },
});

const unknown = instrument({
  instance_id: "widget0",
  kind: "widget",
  name: "widget",
  state: { spin: 3 },
});

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <InstrumentsPage />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  listInstruments.mockResolvedValue({ instruments: [psu, unknown] });
  rescanInstruments.mockResolvedValue({ instruments: [psu] });
  sendInstrumentCommand.mockResolvedValue({ state: {}, result: {} });
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("InstrumentsPage", () => {
  it("lists every instrument with its kind and availability", async () => {
    renderPage();
    expect(await screen.findByRole("heading", { name: "psu" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "widget" })).toBeInTheDocument();
    expect(screen.getAllByText("AVAILABLE")).toHaveLength(2);
  });

  it("draws every panel from the declared state and commands", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "psu" });
    expect(screen.getByText("channels.1.voltage")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Set Output" })).toBeInTheDocument();
    expect(screen.getByText("spin")).toBeInTheDocument();
    expect(screen.getByText("Takes no commands.")).toBeInTheDocument();
  });

  it("renders an unavailable instrument read-only, with the reason", async () => {
    listInstruments.mockResolvedValue({
      instruments: [
        instrument({
          available: false,
          commands: [{ name: "ping", label: "Ping", fields: [] }],
          name: "offline",
          state: { error: "no serial port" },
        }),
      ],
    });
    renderPage();
    expect(await screen.findAllByText("no serial port")).not.toHaveLength(0);
    expect(screen.getByText("UNAVAILABLE")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Ping" })).toBeDisabled();
  });

  it("re-probes on demand", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "psu" });
    await userEvent.click(screen.getByRole("button", { name: /scan/i }));
    expect(rescanInstruments).toHaveBeenCalled();
  });

  it("posts a command to the instrument it belongs to", async () => {
    renderPage();
    const card = (await screen.findByRole("heading", { name: "psu" })).closest(
      ".instruments-page__card"
    ) as HTMLElement;
    await userEvent.click(within(card).getByLabelText("Enabled"));
    await userEvent.click(within(card).getByRole("button", { name: "Set Output" }));
    expect(sendInstrumentCommand).toHaveBeenCalledWith("psu", "set_output", {
      channel: "1",
      enabled: true,
    });
  });

  it("spins while the instruments are being read", () => {
    listInstruments.mockReturnValue(pending());
    renderPage();
    expect(spinners()).toHaveLength(1);
    expect(screen.queryByText("No instruments detected")).not.toBeInTheDocument();
  });

  it("reports instruments that could not be read", async () => {
    listInstruments.mockRejectedValue(new Error("registry unreachable"));
    renderPage();
    expect(await screen.findByText("Could not read the instruments")).toBeInTheDocument();
    expect(screen.getByText("registry unreachable")).toBeInTheDocument();
  });

  it("says so when nothing is registered", async () => {
    listInstruments.mockResolvedValue({ instruments: [] });
    renderPage();
    expect(await screen.findByText("No instruments detected")).toBeInTheDocument();
  });
});

describe("InstrumentsPage shows what a command answered with", () => {
  const camera = instrument({
    commands: [{ name: "snapshot", label: "Take Snapshot", fields: [], returns: "image" }],
    instance_id: "cam0",
    kind: "camera",
    name: "camera",
    primary_command: "snapshot",
  });

  function cardOf(name: string): HTMLElement {
    return screen.getByRole("heading", { name }).closest(".instruments-page__card") as HTMLElement;
  }

  it("draws a PNG the command answered with on that instrument's panel only", async () => {
    listInstruments.mockResolvedValue({ instruments: [camera, psu] });
    sendInstrumentCommand.mockResolvedValue({ result: { image_base64: "AAA" }, state: {} });
    renderPage();
    await screen.findByRole("heading", { name: "camera" });

    await userEvent.click(within(cardOf("camera")).getByRole("button", { name: "Capture" }));

    const image = await within(cardOf("camera")).findByRole("img", { name: /camera answered/ });
    expect(image).toHaveAttribute("src", "data:image/png;base64,AAA");
    expect(within(cardOf("psu")).queryByRole("img", { name: /answered/ })).toBeNull();
  });

  it("reads a JPEG as a JPEG, and takes it off the panel when hidden", async () => {
    listInstruments.mockResolvedValue({ instruments: [camera] });
    sendInstrumentCommand.mockResolvedValue({
      result: { image_base64: "BBB", suffix: ".jpg" },
      state: {},
    });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Capture" }));
    const image = await screen.findByRole("img", { name: /camera answered/ });
    expect(image).toHaveAttribute("src", "data:image/jpeg;base64,BBB");

    await userEvent.click(screen.getByRole("button", { name: "Hide" }));

    expect(screen.queryByRole("img", { name: /camera answered/ })).not.toBeInTheDocument();
  });

  it("keeps the last picture when a command answers without one", async () => {
    listInstruments.mockResolvedValue({ instruments: [camera] });
    sendInstrumentCommand.mockResolvedValueOnce({ result: { image_base64: "AAA" }, state: {} });
    sendInstrumentCommand.mockResolvedValueOnce({ result: { image_base64: "" }, state: {} });
    renderPage();
    await userEvent.click(await screen.findByRole("button", { name: "Capture" }));
    await screen.findByRole("img", { name: /camera answered/ });

    await userEvent.click(screen.getByRole("button", { name: "Capture" }));

    await waitFor(() => expect(sendInstrumentCommand).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("img", { name: /camera answered/ })).toHaveAttribute(
      "src",
      "data:image/png;base64,AAA"
    );
  });

  it("shows a refused command's reason on that instrument, and clears it once one succeeds", async () => {
    sendInstrumentCommand.mockRejectedValueOnce(new Error("channel 1 is locked"));
    renderPage();
    await screen.findByRole("heading", { name: "psu" });
    const button = within(cardOf("psu")).getByRole("button", { name: "Set Output" });

    await userEvent.click(button);
    expect(await within(cardOf("psu")).findByRole("alert")).toHaveTextContent(
      "channel 1 is locked"
    );
    expect(within(cardOf("widget")).queryByRole("alert")).toBeNull();

    await userEvent.click(button);
    await waitFor(() => expect(within(cardOf("psu")).queryByRole("alert")).toBeNull());
  });

  it("shows what a scan found in place of the list it had", async () => {
    renderPage();
    await screen.findByRole("heading", { name: "widget" });

    await userEvent.click(screen.getByRole("button", { name: /scan/i }));

    await waitFor(() =>
      expect(screen.queryByRole("heading", { name: "widget" })).not.toBeInTheDocument()
    );
    expect(screen.getByRole("heading", { name: "psu" })).toBeInTheDocument();
  });
});
