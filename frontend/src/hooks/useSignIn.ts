import { useSyncExternalStore } from "react";

import type { Provenance } from "@api/types";

/**
 * Who is at the bench, where, and which test session they are working in.
 *
 * Not an account: nothing needs it, and Gauntlet checks none of it. It is kept
 * per browser and stamped onto the runs this browser starts and the notes it
 * writes, as their provenance.
 */
export interface SignIn {
  location: string;
  name: string;
  session: string;
}

const STORAGE_KEY = "gauntlet:sign-in";

const listeners = new Set<() => void>();

// `useSyncExternalStore` compares snapshots by identity, so the parsed value
// is reused for as long as the stored text is unchanged.
let lastRaw: string | null = null;
let lastValue: SignIn | null = null;

function readRaw(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

function parse(raw: string | null): SignIn | null {
  if (raw === null) return null;
  try {
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return null;
    const { location, name, session } = parsed as Record<string, unknown>;
    if (typeof name !== "string" || typeof location !== "string" || typeof session !== "string") {
      return null;
    }
    return { location, name, session };
  } catch {
    return null;
  }
}

function snapshot(): SignIn | null {
  const raw = readRaw();
  if (raw !== lastRaw) {
    lastRaw = raw;
    lastValue = parse(raw);
  }
  return lastValue;
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  // Another tab signing in or out writes the same key.
  window.addEventListener("storage", listener);
  return () => {
    listeners.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

/** Sign in, or out with `null`, for every component in this browser. */
export function setSignIn(next: SignIn | null): void {
  try {
    if (next === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  } catch {
    // Storage can be full or disabled; the sign-in then does not stick.
  }
  for (const listener of listeners) listener();
}

/** What a run or note records about who made it, empty when nobody signed in. */
export function provenanceOf(signIn: SignIn | null): Provenance {
  return {
    location: signIn?.location || null,
    operator: signIn?.name || null,
    session: signIn?.session || null,
  };
}

/** The current sign-in, or `null` when nobody has signed in in this browser. */
export function useSignIn(): SignIn | null {
  return useSyncExternalStore(subscribe, snapshot);
}

export default useSignIn;
