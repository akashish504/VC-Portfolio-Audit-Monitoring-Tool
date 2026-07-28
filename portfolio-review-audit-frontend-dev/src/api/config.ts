// src/api/config.ts

// 1. Define the shape of the window object to include _env_
// This prevents TypeScript errors when accessing window._env_
declare global {
  interface Window {
    _env_?: {
      // Add any keys you expect to inject from Doppler here
      API_URL?: string;
      VITE_API_BASE_URL?: string;
      VITE_OKTA_ISSUER?: string;
      VITE_OKTA_CLIENT_ID?: string;
      VITE_OKTA_REDIRECT_URI?: string;
      VITE_OKTA_POST_LOGOUT_REDIRECT_URI?: string;
      [key: string]: string | undefined;
    };
  }
}

// 2. Helper function to safely get environment variables
// Strategy:
// - First, check for Runtime Injection (window._env_) -> Used in Production/Docker
// - Second, check for Build-Time Injection (import.meta.env) -> Used in Local Dev
const getEnv = (key: string): string => {
  // SAFE ACCESS: Check if window exists AND if _env_ exists
  if (typeof window !== "undefined" && window._env_ && window._env_[key]) {
    return window._env_[key] as string;
  }

  // Fallback to Vite build-time env (Local Dev)
  return (import.meta as any).env?.[key] || "";
};

const getEnvBool = (key: string, defaultValue: boolean): boolean => {
  const raw = (getEnv(key) || "").trim().toLowerCase();
  if (!raw) return defaultValue;
  if (["1", "true", "yes", "y", "on"].includes(raw)) return true;
  if (["0", "false", "no", "n", "off"].includes(raw)) return false;
  return defaultValue;
};

// 3. Export your constants using the helper
// Normalize base URL:
// - strip trailing `/api` or `/api/v1` to avoid `/api/v1/api/v1/...`
// - drop `.private.` if a private DNS name is injected into browser config
function normalizeApiBaseUrl(raw: string): string {
  if (!raw) {
    // Default to same-origin so deployments can rely on the nginx `/api` proxy
    // and avoid CORS entirely.
    if (typeof window !== "undefined" && window.location?.origin) {
      return window.location.origin;
    }
    return raw;
  }
  let u = raw.trim().replace(/\/+$/, "");
  // If env injects a bare host (no scheme), browsers treat it as a relative path and
  // you end up with `${window.location.origin}/${host}/...`. Default to https.
  if (!/^https?:\/\//i.test(u)) {
    u = `https://${u.replace(/^\/+/, '')}`;
  }
  u = u.replace(/\/api\/v1$/i, "");
  u = u.replace(/\/api$/i, "");
  u = u.replace(".private.", ".");
  return u;
}

export const API_BASE = normalizeApiBaseUrl(getEnv("VITE_API_BASE_URL"));
export const OKTA_ISSUER = getEnv("VITE_OKTA_ISSUER");
export const OKTA_CLIENT_ID = getEnv("VITE_OKTA_CLIENT_ID");
export const OKTA_REDIRECT_URI = getEnv("VITE_OKTA_REDIRECT_URI");
/** If set, must match a "Sign-out redirect URI" in the Okta OIDC app (often same as app origin). */
export const OKTA_POST_LOGOUT_REDIRECT_URI = getEnv("VITE_OKTA_POST_LOGOUT_REDIRECT_URI");

// Toggles: local dev defaults to **off** (no Okta / no CSRF) unless you set env in `.env.local`.
// Set `VITE_ENABLE_OKTA=true` and `VITE_ENABLE_CSRF=true` in deployment (Doppler, k8s, or `window._env_`).
export const ENABLE_OKTA = getEnvBool("VITE_ENABLE_OKTA", false);
export const ENABLE_CSRF = getEnvBool("VITE_ENABLE_CSRF", false);

