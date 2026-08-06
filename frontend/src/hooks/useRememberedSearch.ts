import { useEffect, useRef } from "react";
import { useSearchParams } from "react-router";

/**
 * Carry a page's chosen search parameters across visits, through
 * `localStorage`.
 *
 * The URL stays the source of truth: a link that names any of `keys` is
 * honoured as written, and only a visit that names none of them is restored
 * from what was last seen. That way a shared link still opens what it points
 * at, while reaching the page from the navigation bar returns the operator to
 * where they were.
 *
 * A parameter the caller wants remembered has to be set rather than deleted
 * when it returns to its default, because an absent parameter is what marks a
 * visit as fresh.
 *
 * Corrupt, full or disabled storage is treated as nothing remembered rather
 * than as an error: this is a convenience, not a source of truth.
 */
export function useRememberedSearch(storageKey: string, keys: string[]): void {
  const [searchParams, setSearchParams] = useSearchParams();
  // Restoring is a once-per-mount decision. Without this a later clearing of
  // the parameters would be read as a fresh visit and undone.
  const restored = useRef(false);

  useEffect(() => {
    if (restored.current) return;
    restored.current = true;
    if (keys.some((key) => searchParams.has(key))) return;

    let saved: string | null = null;
    try {
      saved = localStorage.getItem(storageKey);
    } catch {
      return;
    }
    if (!saved) return;
    setSearchParams(new URLSearchParams(saved), { replace: true });
  }, [keys, searchParams, setSearchParams, storageKey]);

  useEffect(() => {
    const remembered = new URLSearchParams();
    for (const key of keys) {
      const value = searchParams.get(key);
      if (value !== null) remembered.set(key, value);
    }
    // A visit carrying none of the keys is the one about to be restored, so
    // writing here would erase what it is about to read.
    if (remembered.toString() === "") return;
    try {
      localStorage.setItem(storageKey, remembered.toString());
    } catch {
      // Storage can be full or disabled; the page still works, it just will
      // not remember where it was.
    }
  }, [keys, searchParams, storageKey]);
}

export default useRememberedSearch;
