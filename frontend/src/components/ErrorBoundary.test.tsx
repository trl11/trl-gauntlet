import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import ErrorBoundary from "./ErrorBoundary";

/** React logs the caught error; the test asserts on the fallback, not the noise. */
beforeEach(() => {
  vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  vi.restoreAllMocks();
});

const Boom: React.FC<{ throws?: boolean }> = ({ throws = true }) => {
  if (throws) throw new Error("the chart exploded");
  return <p>recovered view</p>;
};

describe("ErrorBoundary", () => {
  it("renders its children while nothing throws", () => {
    render(
      <ErrorBoundary>
        <p>the view</p>
      </ErrorBoundary>
    );

    expect(screen.getByText("the view")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("replaces a view that threw with the failure message", () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );

    expect(screen.getByRole("alert")).toHaveTextContent(
      "Something broke while rendering this view"
    );
    expect(screen.getByText("the chart exploded")).toBeInTheDocument();
  });

  it("renders the children that come after a retry", async () => {
    const Flaky: React.FC = () => {
      const [throws, setThrows] = useState(true);
      return (
        <>
          <button onClick={() => setThrows(false)}>fix it</button>
          <ErrorBoundary>
            <Boom throws={throws} />
          </ErrorBoundary>
        </>
      );
    };
    render(<Flaky />);
    expect(screen.getByRole("alert")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "fix it" }));
    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(screen.getByText("recovered view")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the fallback again when the retry does not help", async () => {
    render(
      <ErrorBoundary>
        <Boom />
      </ErrorBoundary>
    );

    await userEvent.click(screen.getByRole("button", { name: "Try again" }));

    expect(screen.getByRole("alert")).toBeInTheDocument();
  });
});
