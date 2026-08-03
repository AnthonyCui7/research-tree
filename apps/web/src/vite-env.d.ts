/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_RESEARCH_TREE_API_BASE_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
