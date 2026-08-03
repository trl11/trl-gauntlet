/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute base the REST API is reached at. Empty means same origin. */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

/** What the Electron preload script exposes to the bundle. */
interface GauntletHost {
  /**
   * Absolute base the backend is listening on, such as
   * `http://127.0.0.1:41287`. The port is chosen when the app starts, so this
   * is the only way the bundle can learn it.
   */
  readonly apiBase: string;
}

interface Window {
  /** Present only when the bundle runs inside the Electron app. */
  readonly gauntlet?: GauntletHost;
}
