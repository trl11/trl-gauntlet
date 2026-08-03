/**
 * The banner watches the whole react-query cache, so it reports an outage
 * rather than one endpoint answering 404.
 */

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@api/client";

import ApiErrorBanner from "./ApiErrorBanner";

const DELAY_MS = 5000;

let client: QueryClient;

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Infinity } },
  });
});

afterEach(() => {
  // Unmount before emptying the cache: clearing it notifies subscribers, and a
  // still-mounted banner would react to that outside any act scope.
  cleanup();
  client.clear();
  vi.useRealTimers();
});

function mount() {
  return render(
    <QueryClientProvider client={client}>
      <ApiErrorBanner />
    </QueryClientProvider>
  );
}

async function failWith(error: unknown, key = "health"): Promise<void> {
  await act(async () => {
    await client
      .fetchQuery({ queryKey: [key], queryFn: () => Promise.reject(error) })
      .catch(() => {});
    // react-query delivers cache notifications on a zero-delay timer, so the
    // banner's state update lands here rather than after act has exited.
    await vi.advanceTimersByTimeAsync(0);
  });
}

async function waitOutTheDelay(): Promise<void> {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(DELAY_MS);
  });
}

describe("while the backend answers", () => {
  it("shows nothing", async () => {
    mount();
    await act(async () => {
      await client.fetchQuery({
        queryKey: ["health"],
        queryFn: () => Promise.resolve({ ok: true }),
      });
    });
    await waitOutTheDelay();

    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("once requests start failing", () => {
  it("stays hidden until the outage has lasted long enough", async () => {
    mount();
    await failWith(new ApiError(0, "cannot reach the Gauntlet API", "/api/health"));

    expect(screen.queryByRole("status")).toBeNull();

    await waitOutTheDelay();

    expect(screen.getByRole("status")).toHaveTextContent(/is not responding/);
  });

  it("names where it was looking", async () => {
    mount();
    await failWith(new ApiError(0, "cannot reach the Gauntlet API", "/api/health"));
    await waitOutTheDelay();

    expect(screen.getByRole("status")).toHaveTextContent(window.location.host);
  });

  it("treats a server error as an outage", async () => {
    mount();
    await failWith(new ApiError(503, "unavailable", "/api/health"));
    await waitOutTheDelay();

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("treats a failure that is not an ApiError as an outage", async () => {
    mount();
    await failWith(new TypeError("boom"));
    await waitOutTheDelay();

    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("ignores a single endpoint answering 404", async () => {
    mount();
    await failWith(new ApiError(404, "unknown run", "/api/runs/r9"));
    await waitOutTheDelay();

    expect(screen.queryByRole("status")).toBeNull();
  });

  it("ignores a rejected request", async () => {
    mount();
    await failWith(new ApiError(422, "undeclared override", "/api/runs"));
    await waitOutTheDelay();

    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("recovery", () => {
  it("clears the banner once a query succeeds again", async () => {
    mount();
    await failWith(new ApiError(0, "cannot reach the Gauntlet API", "/api/health"));
    await waitOutTheDelay();
    expect(screen.getByRole("status")).toBeInTheDocument();

    await act(async () => {
      client.clear();
    });

    await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  });

  it("says it is reconnecting while a request is in flight", async () => {
    mount();
    await failWith(new ApiError(0, "cannot reach the Gauntlet API", "/api/health"));
    await waitOutTheDelay();

    act(() => {
      client
        .fetchQuery({ queryKey: ["version"], queryFn: () => new Promise(() => {}) })
        .catch(() => {});
    });

    await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent(/reconnecting/));
  });
});
