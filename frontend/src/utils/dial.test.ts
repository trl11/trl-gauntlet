import { describe, expect, it } from "vitest";

import { fractionOf, snap, valueAtPoint } from "./dial";

const BOX = { height: 100, left: 0, top: 0, width: 100 };

describe("fractionOf", () => {
  it("measures how far along its travel a setting sits", () => {
    expect(fractionOf(15, 0, 30)).toBe(0.5);
  });

  it("holds at an end for a setting outside the range", () => {
    expect(fractionOf(-4, 0, 30)).toBe(0);
    expect(fractionOf(44, 0, 30)).toBe(1);
  });

  it("reads a range with no width as its lowest setting", () => {
    expect(fractionOf(5, 5, 5)).toBe(0);
  });
});

describe("snap", () => {
  it("rounds to a whole number of steps without floating point dust", () => {
    expect(snap(5.07, 0, 30, 0.1)).toBe(5.1);
    expect(snap(4501.4, 0, 12000, 100)).toBe(4500);
  });

  it("keeps the result inside the range", () => {
    expect(snap(-2, 0, 30, 0.1)).toBe(0);
    expect(snap(99, 0, 30, 0.1)).toBe(30);
  });
});

describe("valueAtPoint", () => {
  it("reads the top of the dial as the middle of its range", () => {
    expect(valueAtPoint(BOX, { x: 50, y: 0 }, 0, 30, 0.1)).toBe(15);
  });

  it("reads the ends of the sweep as the ends of the range", () => {
    expect(valueAtPoint(BOX, { x: 0, y: 100 }, 0, 30, 0.1)).toBe(0);
    expect(valueAtPoint(BOX, { x: 100, y: 100 }, 0, 30, 0.1)).toBe(30);
  });

  it("clamps the part of the circle the sweep does not reach", () => {
    expect(valueAtPoint(BOX, { x: 49, y: 100 }, 0, 30, 0.1)).toBe(0);
    expect(valueAtPoint(BOX, { x: 51, y: 100 }, 0, 30, 0.1)).toBe(30);
  });

  it("snaps where it lands to a whole number of steps", () => {
    expect(valueAtPoint(BOX, { x: 100, y: 50 }, 0, 10, 0.1)).toBe(8.3);
  });
});
