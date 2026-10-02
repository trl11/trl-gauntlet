import { describe, expect, it } from "vitest";

import type { StreamChannel } from "@api/types";
import { defaultSettings, limitsOf, pickableSettings, startInstrument } from "./daq_limits";

const CHANNELS: StreamChannel[] = [
  { key: "1", label: "Rail", max: 10, min: -10, unit: "V" },
  { key: "2", label: "Aux", max: 5, min: -5, unit: "V" },
];

describe("defaultSettings", () => {
  it("starts every channel at the range it can read", () => {
    expect(defaultSettings(CHANNELS).entries).toEqual({
      "1": { high: "10", low: "-10" },
      "2": { high: "5", low: "-5" },
    });
  });

  it("starts from the limits the run already holds", () => {
    const held = { channels: { "1": { high: 3.3, low: null } }, post_s: 1, pre_s: 4 };
    const settings = defaultSettings(CHANNELS, held);
    expect(settings.entries["1"]).toEqual({ high: "3.3", low: "-10" });
    expect(settings.before).toBe("4");
    expect(settings.after).toBe("1");
  });

  it("leaves an entry empty for a channel that reports no range", () => {
    expect(defaultSettings([{ key: "1", label: "Rail", unit: "V" }]).entries["1"]).toEqual({
      high: "",
      low: "",
    });
  });
});

describe("enabling channels", () => {
  const WITH_ONE_OFF: StreamChannel[] = [CHANNELS[0], { ...CHANNELS[1], enabled: false }];

  it("starts each channel as the instrument has it", () => {
    expect(defaultSettings(WITH_ONE_OFF).enabled).toEqual({ "1": true, "2": false });
  });

  it("watches nothing on a channel that is turned off", () => {
    const settings = defaultSettings(CHANNELS);
    settings.entries["2"].low = "1";
    settings.enabled["2"] = false;
    expect(limitsOf(settings, CHANNELS)["2"]).toBeUndefined();
  });

  it("starts with no channel picked", () => {
    expect(pickableSettings(CHANNELS).enabled).toEqual({ "1": false, "2": false });
  });

  it("scans only the channels picked, even when nothing is watched", () => {
    const settings = pickableSettings(CHANNELS);
    settings.enabled["1"] = true;
    expect(startInstrument(settings, CHANNELS)).toEqual({
      channels: {},
      enabled: { "1": true, "2": false },
      post_s: 2,
      pre_s: 2,
    });
  });

  it("leaves the instrument as it is while no channel is picked", () => {
    const settings = pickableSettings(CHANNELS);
    settings.entries["1"].high = "3";
    expect(startInstrument(settings, CHANNELS)).toBeNull();
  });
});

describe("limitsOf", () => {
  it("watches nothing at the ends of the range", () => {
    expect(limitsOf(defaultSettings(CHANNELS), CHANNELS)["1"]).toEqual({ high: null, low: null });
  });

  it("watches a limit moved inside the range", () => {
    const settings = defaultSettings(CHANNELS);
    settings.entries["1"].high = "3.3";
    expect(limitsOf(settings, CHANNELS)["1"]).toEqual({ high: 3.3, low: null });
  });

  it("reads an empty entry as no limit", () => {
    const settings = defaultSettings(CHANNELS);
    settings.entries["2"].low = "  ";
    expect(limitsOf(settings, CHANNELS)["2"].low).toBeNull();
  });

  it("refuses an entry that is not a number, naming the channel", () => {
    const settings = defaultSettings(CHANNELS);
    settings.entries["2"].low = "abc";
    expect(() => limitsOf(settings, CHANNELS)).toThrow("Aux: a limit must be a number");
  });
});

describe("startInstrument", () => {
  it("is null while no channel is picked", () => {
    expect(startInstrument(pickableSettings(CHANNELS), CHANNELS)).toBeNull();
  });

  it("sends only the channels that are watched, with the window", () => {
    const settings = pickableSettings(CHANNELS);
    settings.enabled["2"] = true;
    settings.entries["2"].low = "1";
    settings.before = "0.5";
    expect(startInstrument(settings, CHANNELS)).toEqual({
      channels: { "2": { high: null, low: 1 } },
      enabled: { "1": false, "2": true },
      post_s: 2,
      pre_s: 0.5,
    });
  });
});
