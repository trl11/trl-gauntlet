/**
 * Session cookies for view preferences that should last until the browser
 * closes, and no longer.
 */

/** The value of one cookie, or null when it is not set. */
export function readCookie(name: string): string | null {
  const prefix = `${encodeURIComponent(name)}=`;
  const found = document.cookie.split("; ").find((entry) => entry.startsWith(prefix));
  return found === undefined ? null : decodeURIComponent(found.slice(prefix.length));
}

/** Set a cookie with no expiry, so the browser drops it when the session ends. */
export function writeCookie(name: string, value: string): void {
  document.cookie = `${encodeURIComponent(name)}=${encodeURIComponent(value)}; path=/; SameSite=Lax`;
}
