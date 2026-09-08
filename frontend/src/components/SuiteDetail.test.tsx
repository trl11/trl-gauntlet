import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { Suite } from "@api/types";

import SuiteDetail from "./SuiteDetail";
import { suite as capturedSuite } from "../test/fixtures";

function suite(partial: Partial<Suite> = {}): Suite {
  return { ...capturedSuite, ...partial };
}

function renderDetail(value: Suite) {
  return render(
    <SuiteDetail
      onEditProfile={() => {}}
      onSelectProfile={() => {}}
      onStart={() => {}}
      selectedProfile={null}
      suite={value}
      unmet={[]}
      verify={null}
    />
  );
}

describe("SuiteDetail", () => {
  it("offers each declared download as a link to the suite's endpoint", () => {
    renderDetail(
      suite({
        downloads: [
          {
            description: "The image the part is programmed with.",
            label: "PMU3 firmware",
            path: "firmware/x.hex",
          },
        ],
        key: "tid_pic18f26k83",
      })
    );
    const link = screen.getByRole("link", { name: "PMU3 firmware" });
    expect(link).toHaveAttribute("href", "/api/suites/tid_pic18f26k83/downloads/firmware/x.hex");
    expect(link).toHaveAttribute("download");
    expect(screen.getByText("The image the part is programmed with.")).toBeInTheDocument();
  });

  it("falls back to the filename when a download declares no label", () => {
    renderDetail(suite({ downloads: [{ description: "", label: "", path: "docs/wiring.pdf" }] }));
    expect(screen.getByRole("link", { name: "wiring.pdf" })).toBeInTheDocument();
  });

  it("shows no download list when the suite declares none", () => {
    renderDetail(suite({ downloads: [] }));
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });

  it("turns a url in the setup text into a link", () => {
    renderDetail(suite({ setup: "Source is https://example.test/repo before a run." }));
    expect(screen.getByRole("link", { name: "https://example.test/repo" })).toHaveAttribute(
      "href",
      "https://example.test/repo"
    );
  });
});
