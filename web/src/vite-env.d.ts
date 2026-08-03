/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Absolute base the REST API is reached at. Empty means same origin. */
  readonly VITE_API_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
