import { describe, expect, it } from "vitest";

import { linkify } from "./linkify";

describe("linkify", () => {
  it("returns text with no url as one plain part", () => {
    expect(linkify("nothing to click")).toEqual([{ text: "nothing to click" }]);
  });

  it("splits a url out of the text around it", () => {
    expect(linkify("see https://example.com/x for more")).toEqual([
      { text: "see " },
      { href: "https://example.com/x", text: "https://example.com/x" },
      { text: " for more" },
    ]);
  });

  it("leaves a sentence's punctuation outside the link", () => {
    const parts = linkify("at https://example.com/repo.");
    expect(parts[1]).toEqual({
      href: "https://example.com/repo",
      text: "https://example.com/repo",
    });
    expect(parts[2]).toEqual({ text: "." });
  });

  it("finds every url in a block", () => {
    const parts = linkify("http://a.test and https://b.test");
    expect(parts.filter((part) => part.href).map((part) => part.href)).toEqual([
      "http://a.test",
      "https://b.test",
    ]);
  });

  it("returns nothing for an empty string", () => {
    expect(linkify("")).toEqual([]);
  });
});
