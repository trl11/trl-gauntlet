import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@api/client";

import ProfileEditor from "./ProfileEditor";

const deleteProfile = vi.fn();
const diffProfile = vi.fn();
const duplicateProfile = vi.fn();
const getProfile = vi.fn();
const getProfileSchema = vi.fn();
const saveProfile = vi.fn();

vi.mock("@api/client", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@api/client")>();
  return {
    ...actual,
    deleteProfile: (...args: unknown[]) => deleteProfile(...args),
    diffProfile: (...args: unknown[]) => diffProfile(...args),
    duplicateProfile: (...args: unknown[]) => duplicateProfile(...args),
    getProfile: (...args: unknown[]) => getProfile(...args),
    getProfileSchema: (...args: unknown[]) => getProfileSchema(...args),
    saveProfile: (...args: unknown[]) => saveProfile(...args),
  };
});

const BODY = "cycles: 3\nlabel: soak\n";

const SCHEMA = {
  type: "object",
  properties: {
    cycles: { type: "integer", title: "Cycles" },
    label: { type: "string", title: "Label" },
  },
};

function renderEditor(props: Partial<React.ComponentProps<typeof ProfileEditor>> = {}) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <ProfileEditor name="mock.yaml" onClose={vi.fn()} suiteKey="thermal_cycle" {...props} />
    </QueryClientProvider>
  );
}

beforeEach(() => {
  getProfile.mockResolvedValue({ body: BODY, name: "mock.yaml", path: "/p/mock.yaml" });
  getProfileSchema.mockResolvedValue(SCHEMA);
  saveProfile.mockResolvedValue({ name: "mock.yaml", path: "/p/mock.yaml", user_authored: true });
  diffProfile.mockResolvedValue({
    diff: "--- a/mock.yaml\n+++ b/mock.yaml\n@@ -1 +1 @@\n-cycles: 3\n+cycles: 9",
  });
  duplicateProfile.mockResolvedValue({
    name: "copy.yaml",
    path: "/p/copy.yaml",
    user_authored: true,
  });
  deleteProfile.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.clearAllMocks();
});

describe("ProfileEditor", () => {
  it("fills the generated form from the profile body", async () => {
    renderEditor();
    expect(await screen.findByLabelText("Cycles")).toHaveValue(3);
    expect(screen.getByLabelText("Label")).toHaveValue("soak");
  });

  it("writes form edits back as YAML and saves them", async () => {
    const user = userEvent.setup();
    renderEditor();
    const label = await screen.findByLabelText("Label");
    await user.clear(label);
    await user.type(label, "burn");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(saveProfile).toHaveBeenCalled());
    expect(saveProfile.mock.calls[0][2]).toContain("label: burn");
  });

  it("edits the raw YAML and reports a parse error", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("tab", { name: "YAML" }));
    const area = screen.getByLabelText("Profile YAML");
    await user.clear(area);
    await user.type(area, "cycles: 3: 4");
    expect(await screen.findByRole("alert")).toBeInTheDocument();
  });

  it("falls back to YAML when the suite publishes no schema", async () => {
    getProfileSchema.mockRejectedValue(new ApiError(404, "no profile schema", "/api"));
    renderEditor();
    expect(await screen.findByText(/publishes no profile schema/)).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Form" })).toBeDisabled();
  });

  it("renders the unified diff the server returns", async () => {
    const user = userEvent.setup();
    renderEditor();
    // The button enables as soon as the profile query settles; the diff is
    // taken against the body, which the form reflects one render later.
    await screen.findByDisplayValue("soak");
    await user.click(await screen.findByRole("button", { name: "Show diff" }));
    const diff = await screen.findByLabelText("Unified diff");
    expect(diff).toHaveTextContent("-cycles: 3");
    expect(diff).toHaveTextContent("+cycles: 9");
    expect(diffProfile).toHaveBeenCalledWith("thermal_cycle", "mock.yaml", BODY);
  });

  it("duplicates the profile under a new name", async () => {
    const user = userEvent.setup();
    const onProfileChanged = vi.fn();
    renderEditor({ onProfileChanged });
    await user.click(await screen.findByRole("button", { name: "Duplicate" }));
    const field = screen.getByLabelText("New profile name");
    await user.clear(field);
    await user.type(field, "copy.yaml");
    await user.click(screen.getByRole("button", { name: "Create copy" }));
    await waitFor(() =>
      expect(duplicateProfile).toHaveBeenCalledWith("thermal_cycle", "mock.yaml", "copy.yaml")
    );
    expect(onProfileChanged).toHaveBeenCalledWith("copy.yaml");
  });

  it("deletes only after the operator confirms", async () => {
    const user = userEvent.setup();
    renderEditor();
    await user.click(await screen.findByRole("button", { name: "Delete" }));
    expect(deleteProfile).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(deleteProfile).toHaveBeenCalledWith("thermal_cycle", "mock.yaml"));
  });

  it("guards unsaved changes when the editor is closed", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    renderEditor({ onClose });
    await user.click(await screen.findByRole("tab", { name: "YAML" }));
    await user.type(screen.getByLabelText("Profile YAML"), "extra: 1\n");
    await user.keyboard("{Escape}");
    expect(onClose).not.toHaveBeenCalled();
    expect(screen.getByText(/Discard unsaved changes/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Confirm" }));
    expect(onClose).toHaveBeenCalled();
  });
});
