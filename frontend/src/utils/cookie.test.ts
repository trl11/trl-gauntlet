import { beforeEach, describe, expect, it } from "vitest";

import { readCookie, writeCookie } from "./cookie";

beforeEach(() => {
  document.cookie = "history_size=; max-age=0; path=/";
  document.cookie = "other=; max-age=0; path=/";
});

describe("cookie", () => {
  it("reads back what was written", () => {
    writeCookie("history_size", "50");
    expect(readCookie("history_size")).toBe("50");
  });

  it("is null for a cookie that was never set", () => {
    expect(readCookie("history_size")).toBeNull();
  });

  it("does not confuse one cookie with another", () => {
    writeCookie("other", "x; y");
    writeCookie("history_size", "20");
    expect(readCookie("other")).toBe("x; y");
    expect(readCookie("history_size")).toBe("20");
  });
});
