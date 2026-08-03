/**
 * The app-wide key bindings: `g` followed by one key navigates, `?` toggles
 * the help overlay, and neither fires while a form control has focus.
 */

import { act, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useGlobalShortcuts } from "./useGlobalShortcuts";

const navigate = vi.fn();

vi.mock("react-router", async () => {
  const actual = await vi.importActual<typeof import("react-router")>("react-router");
  return { ...actual, useNavigate: () => navigate };
});

function press(key: string, init: KeyboardEventInit = {}, target?: Element): void {
  const event = new KeyboardEvent("keydown", { key, bubbles: true, cancelable: true, ...init });
  act(() => {
    (target ?? document).dispatchEvent(event);
  });
}

function mount() {
  return renderHook(() => useGlobalShortcuts(), {
    wrapper: ({ children }) => createElement(MemoryRouter, null, children),
  });
}

beforeEach(() => {
  navigate.mockClear();
  vi.useFakeTimers({ shouldAdvanceTime: true });
});

afterEach(() => {
  vi.useRealTimers();
  document.body.innerHTML = "";
});

describe("the g prefix", () => {
  it.each([
    ["d", "/"],
    ["t", "/tests"],
    ["h", "/history"],
    ["u", "/units"],
    ["i", "/instruments"],
    ["s", "/settings"],
  ])("g then %s navigates to %s", (key, destination) => {
    mount();

    press("g");
    press(key);

    expect(navigate).toHaveBeenCalledWith(destination);
  });

  it("does nothing without the prefix", () => {
    mount();

    press("t");

    expect(navigate).not.toHaveBeenCalled();
  });

  it("forgets the prefix after a second", () => {
    mount();

    press("g");
    vi.advanceTimersByTime(1_001);
    press("t");

    expect(navigate).not.toHaveBeenCalled();
  });

  it("keeps the prefix within the timeout", () => {
    mount();

    press("g");
    vi.advanceTimersByTime(900);
    press("t");

    expect(navigate).toHaveBeenCalledWith("/tests");
  });

  it("a key that names no destination disarms the prefix", () => {
    mount();

    press("g");
    press("z");
    press("t");

    expect(navigate).not.toHaveBeenCalled();
  });

  it("a repeated g stays armed", () => {
    mount();

    press("g");
    press("g");
    press("u");

    expect(navigate).toHaveBeenCalledWith("/units");
  });

  it("the prefix is consumed by a single navigation", () => {
    mount();

    press("g");
    press("t");
    press("h");

    expect(navigate).toHaveBeenCalledTimes(1);
  });
});

describe("the help overlay", () => {
  it("starts closed and lists every shortcut", () => {
    const { result } = mount();

    expect(result.current.helpOpen).toBe(false);
    expect(result.current.shortcuts.map((shortcut) => shortcut.keys)).toContain("g d");
  });

  it("is toggled by the question mark", () => {
    const { result } = mount();

    press("?");
    expect(result.current.helpOpen).toBe(true);

    press("?");
    expect(result.current.helpOpen).toBe(false);
  });

  it("is closed by Escape", () => {
    const { result } = mount();

    press("?");
    press("Escape");

    expect(result.current.helpOpen).toBe(false);
  });

  it("is closed by its own callback", () => {
    const { result } = mount();
    press("?");

    act(() => result.current.closeHelp());

    expect(result.current.helpOpen).toBe(false);
  });

  it("Escape also disarms the prefix", () => {
    mount();

    press("g");
    press("Escape");
    press("t");

    expect(navigate).not.toHaveBeenCalled();
  });
});

describe("keys that belong to something else", () => {
  it.each(["input", "select", "textarea"])("are ignored while a %s has focus", (tag) => {
    const { result } = mount();
    const field = document.createElement(tag);
    document.body.append(field);

    press("?", {}, field);
    press("g", {}, field);
    press("t", {}, field);

    expect(result.current.helpOpen).toBe(false);
    expect(navigate).not.toHaveBeenCalled();
  });

  it("are ignored inside a contenteditable region", () => {
    mount();
    const editor = document.createElement("div");
    editor.contentEditable = "true";
    Object.defineProperty(editor, "isContentEditable", { value: true });
    document.body.append(editor);

    press("g", {}, editor);
    press("t", {}, editor);

    expect(navigate).not.toHaveBeenCalled();
  });

  it.each([{ metaKey: true }, { ctrlKey: true }, { altKey: true }])(
    "are ignored while a modifier is held (%o)",
    (modifier) => {
      mount();

      press("g", modifier);
      press("t", modifier);

      expect(navigate).not.toHaveBeenCalled();
    }
  );

  it("stop being handled once the hook unmounts", () => {
    const { unmount } = mount();
    unmount();

    press("g");
    press("t");

    expect(navigate).not.toHaveBeenCalled();
  });
});
