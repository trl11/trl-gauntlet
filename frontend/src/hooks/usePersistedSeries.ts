import { useState } from "react";

/**
 * A series-name selection that survives a reload, scoped to one
 * `localStorage` key. Starts `null` ("no explicit choice yet, use the
 * caller's default") until the operator picks something, at which point the
 * pick is written back on every change.
 *
 * Corrupt or missing storage is treated the same as no choice yet, rather
 * than as an error: this is a convenience, not a source of truth.
 */
export function usePersistedSeries(storageKey: string): [string[] | null, (next: string[]) => void] {
  const [selected, setSelected] = useState<string[] | null>(() => {
    try {
      const raw = localStorage.getItem(storageKey);
      if (raw == null) return null;
      const parsed: unknown = JSON.parse(raw);
      return Array.isArray(parsed) && parsed.every((entry) => typeof entry === "string") ? parsed : null;
    } catch {
      return null;
    }
  });

  const update = (next: string[]) => {
    setSelected(next);
    try {
      localStorage.setItem(storageKey, JSON.stringify(next));
    } catch {
      // Storage can be full or disabled (private browsing); the pick still
      // works for the rest of this session, it just won't outlive it.
    }
  };

  return [selected, update];
}

export default usePersistedSeries;
