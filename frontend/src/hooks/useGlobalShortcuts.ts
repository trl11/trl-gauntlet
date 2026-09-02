import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router";

/** How long a `g` prefix stays armed before it is forgotten. */
const PREFIX_TIMEOUT_MS = 1000;

const EDITABLE_TAGS = new Set(["input", "select", "textarea"]);

/** Destinations reachable with `g` followed by one key. */
const DESTINATIONS: Record<string, string> = {
  d: "/",
  h: "/history",
  i: "/instruments",
  s: "/system",
  t: "/tests",
  u: "/units",
};

/** One row of the help overlay. */
export interface Shortcut {
  description: string;
  keys: string;
}

/** Every shortcut the app binds, in the order the help overlay lists them. */
const SHORTCUTS: Shortcut[] = [
  { keys: "?", description: "Toggle this help" },
  { keys: "g d", description: "Go to Dashboard" },
  { keys: "g t", description: "Go to Tests" },
  { keys: "g h", description: "Go to History" },
  { keys: "g u", description: "Go to Units" },
  { keys: "g i", description: "Go to Instruments" },
  { keys: "g s", description: "Go to System" },
  { keys: "Esc", description: "Close the open modal or overlay" },
];

/** State of the shortcut help overlay. */
export interface GlobalShortcuts {
  closeHelp: () => void;
  helpOpen: boolean;
  shortcuts: Shortcut[];
}

function isEditable(target: EventTarget | null): boolean {
  const element = target as HTMLElement | null;
  if (element === null) return false;
  if (element.isContentEditable) return true;
  return EDITABLE_TAGS.has(element.tagName?.toLowerCase() ?? "");
}

/**
 * Bind the app-wide keyboard shortcuts and own the help overlay state.
 *
 * Keys are ignored while a form control has focus and while a modifier is
 * held, so nothing here shadows a browser or editor binding.
 */
export function useGlobalShortcuts(): GlobalShortcuts {
  const navigate = useNavigate();
  const [helpOpen, setHelpOpen] = useState(false);
  const armedAt = useRef<number | null>(null);

  const closeHelp = useCallback(() => setHelpOpen(false), []);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (isEditable(event.target)) return;
      if (event.metaKey || event.ctrlKey || event.altKey) return;

      if (event.key === "Escape") {
        armedAt.current = null;
        setHelpOpen(false);
        return;
      }
      if (event.key === "?") {
        event.preventDefault();
        setHelpOpen((open) => !open);
        return;
      }

      const now = Date.now();
      const armed = armedAt.current !== null && now - armedAt.current < PREFIX_TIMEOUT_MS;
      if (armed) {
        armedAt.current = null;
        const destination = DESTINATIONS[event.key];
        if (destination !== undefined) {
          event.preventDefault();
          navigate(destination);
          return;
        }
      }

      armedAt.current = event.key === "g" ? now : null;
    };

    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [navigate]);

  return { closeHelp, helpOpen, shortcuts: SHORTCUTS };
}

export default useGlobalShortcuts;
