import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { metricsSeriesKey, usePersistedSeries } from "./usePersistedSeries";

const KEY = metricsSeriesKey("RUN-1");

describe("usePersistedSeries", () => {
  it("keys each run's pick by its run id", () => {
    expect(KEY).toBe("gauntlet:run:RUN-1:metrics-series");
  });

  it("starts with no choice when nothing is stored", () => {
    const { result } = renderHook(() => usePersistedSeries(KEY));

    expect(result.current[0]).toBeNull();
  });

  it("restores a pick stored earlier", () => {
    localStorage.setItem(KEY, JSON.stringify(["rail.volts", "rail.amps"]));

    const { result } = renderHook(() => usePersistedSeries(KEY));

    expect(result.current[0]).toEqual(["rail.volts", "rail.amps"]);
  });

  it("treats a stored value that is not a list of names as no choice", () => {
    localStorage.setItem(KEY, JSON.stringify(["rail.volts", 3]));
    expect(renderHook(() => usePersistedSeries(KEY)).result.current[0]).toBeNull();

    localStorage.setItem(KEY, JSON.stringify({ series: "rail.volts" }));
    expect(renderHook(() => usePersistedSeries(KEY)).result.current[0]).toBeNull();
  });

  it("treats corrupt storage as no choice", () => {
    localStorage.setItem(KEY, "[not json");

    expect(renderHook(() => usePersistedSeries(KEY)).result.current[0]).toBeNull();
  });

  it("writes a new pick back so it outlives a reload", () => {
    const { result } = renderHook(() => usePersistedSeries(KEY));

    act(() => result.current[1](["cpu.percent"]));

    expect(result.current[0]).toEqual(["cpu.percent"]);
    expect(localStorage.getItem(KEY)).toBe('["cpu.percent"]');
  });

  it("keeps a pick for the session when storage refuses it", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("QuotaExceededError");
    });
    const { result } = renderHook(() => usePersistedSeries(KEY));

    act(() => result.current[1](["cpu.percent"]));

    expect(result.current[0]).toEqual(["cpu.percent"]);
  });
});
