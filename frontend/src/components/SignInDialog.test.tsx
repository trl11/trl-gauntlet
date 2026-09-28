import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { SignIn } from "@hooks/useSignIn";

import SignInDialog from "./SignInDialog";

const getRunProvenance = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return { ...actual, getRunProvenance: () => getRunProvenance() };
});

function renderDialog(current: SignIn | null = null, onClose = vi.fn()) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <SignInDialog current={current} onClose={onClose} />
    </QueryClientProvider>
  );
}

function stored(): unknown {
  const raw = localStorage.getItem("gauntlet:sign-in");
  return raw === null ? null : JSON.parse(raw);
}

beforeEach(() => {
  getRunProvenance.mockResolvedValue({
    locations: ["Lab 2"],
    operators: ["Ada"],
    sessions: ["week 1", "week 2"],
  });
});

describe("SignInDialog", () => {
  it("will not sign in until every field is filled", async () => {
    const user = userEvent.setup();
    renderDialog();
    await user.type(screen.getByLabelText("Name"), "Ada");
    await user.type(screen.getByLabelText("Location"), "Lab 2");
    expect(screen.getByRole("button", { name: "Sign in" })).toBeDisabled();
  });

  it("keeps the sign-in, trimmed, and closes", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderDialog(null, onClose);
    await user.type(screen.getByLabelText("Name"), " Ada ");
    await user.type(screen.getByLabelText("Location"), "Lab 2");
    await user.type(screen.getByLabelText("Test session"), "week 1");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(stored()).toEqual({ location: "Lab 2", name: "Ada", session: "week 1" });
    expect(onClose).toHaveBeenCalled();
  });

  it("starts from the current sign-in and can sign out", async () => {
    const user = userEvent.setup();
    localStorage.setItem(
      "gauntlet:sign-in",
      JSON.stringify({ location: "L", name: "N", session: "S" })
    );
    renderDialog({ location: "L", name: "N", session: "S" });
    expect(screen.getByLabelText("Name")).toHaveValue("N");
    await user.click(screen.getByRole("button", { name: "Sign out" }));
    expect(stored()).toBeNull();
  });

  it("offers the sessions earlier runs recorded", async () => {
    const { container } = renderDialog();
    const field = screen.getByLabelText("Test session");
    await waitFor(() => {
      const list = container.querySelector(`datalist#${CSS.escape(field.getAttribute("list")!)}`);
      expect([...(list?.querySelectorAll("option") ?? [])].map((o) => o.value)).toEqual([
        "week 1",
        "week 2",
      ]);
    });
  });
});
