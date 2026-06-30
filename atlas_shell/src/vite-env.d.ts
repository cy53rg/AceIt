/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_ATLAS_DAEMON_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
