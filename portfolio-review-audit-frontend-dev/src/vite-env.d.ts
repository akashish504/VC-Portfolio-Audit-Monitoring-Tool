/// <reference types="vite/client" />

import "axios";

declare module "axios" {
  interface InternalAxiosRequestConfig {
    /** When true, do not show the global in-flight request indicator. */
    skipGlobalLoading?: boolean;
  }
}

declare module "xlsx-populate";

declare global {
  interface ImportMetaEnv {
    readonly [key: string]: string | boolean | undefined;
  }

  interface ImportMeta {
    readonly env: ImportMetaEnv;
  }

  interface Window {
    _env_?: {
      [key: string]: string;
    };
  }
}

export {};
