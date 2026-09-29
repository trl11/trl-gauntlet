import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { provenanceOf, setCheckIn, useCheckIn } from "./useCheckIn";

const STORAGE_KEY = "gauntlet:check-in";
const ADA = { location: "Bench 3", name: "Ada", session: "TID-7" };

describe("useCheckIn", () => {
  it("is empty when nobody has checked in", () => {
    expect(renderHook(() => useCheckIn()).result.current).toBeNull();
  });

  it("follows a check-in and a check-out made anywhere in this browser", () => {
    const { result } = renderHook(() => useCheckIn());

    act(() => setCheckIn(ADA));
    expect(result.current).toEqual(ADA);

    act(() => setCheckIn(null));
    expect(result.current).toBeNull();
  });

  it("picks up a check-in another tab wrote", () => {
    const { result } = renderHook(() => useCheckIn());

    act(() => {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(ADA));
      window.dispatchEvent(new StorageEvent("storage", { key: STORAGE_KEY }));
    });

    expect(result.current).toEqual(ADA);
  });

  it("ignores a stored check-in missing a field or of the wrong shape", () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ location: "Bench 3", name: "Ada" }));
    expect(renderHook(() => useCheckIn()).result.current).toBeNull();

    localStorage.setItem(STORAGE_KEY, JSON.stringify("Ada"));
    expect(renderHook(() => useCheckIn()).result.current).toBeNull();
  });

  it("ignores a stored check-in that is not JSON", () => {
    localStorage.setItem(STORAGE_KEY, "{Ada");

    expect(renderHook(() => useCheckIn()).result.current).toBeNull();
  });

  it("stays checked out, without an error, when storage refuses the write", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const { result } = renderHook(() => useCheckIn());

    expect(() => act(() => setCheckIn(ADA))).not.toThrow();
    expect(result.current).toBeNull();
  });
});

describe("provenanceOf", () => {
  it("records who, where and which session from a check-in", () => {
    expect(provenanceOf(ADA)).toEqual({ location: "Bench 3", operator: "Ada", session: "TID-7" });
  });

  it("records nothing for no check-in or a blank field", () => {
    expect(provenanceOf(null)).toEqual({ location: null, operator: null, session: null });
    expect(provenanceOf({ ...ADA, session: "" }).session).toBeNull();
  });
});
