import axios, { AxiosError, InternalAxiosRequestConfig, AxiosResponse } from 'axios';
import { API_BASE } from './config';
import { oktaAuth } from '../auth/okta';
import { ENABLE_CSRF, ENABLE_OKTA } from './config';
import { assertSafeOutgoingRequest } from './httpHeaderGuard';
import { beginApiRequest, endApiRequest } from './globalApiLoadingStore';

const GET_USER_PATH = '/api/v1/user/get-user';
const CSRF_RELOAD_KEY = 'csrf_reload_attempted';

class TokenStore {
  private _csrfToken: string | null = null;
  private _bootstrapped = false;
  private _lastUpdated = 0;
  private _isInitializing = false;
  private _initializationPromise: Promise<unknown> | null = null;

  setCsrfToken(token: string) {
    this._csrfToken = token;
    this._lastUpdated = Date.now();
  }

  getCsrfToken() {
    return this._csrfToken;
  }

  markBootstrapped() {
    this._bootstrapped = true;
    this._lastUpdated = Date.now();
  }

  hasTokens(): boolean {
    // CSRF present and we've completed the initial get-user once.
    return this._bootstrapped && this._csrfToken !== null;
  }

  clear() {
    this._csrfToken = null;
    this._bootstrapped = false;
    this._lastUpdated = 0;
    this._isInitializing = false;
    this._initializationPromise = null;
  }

  getLastUpdated() { return this._lastUpdated; }
  isInitializing() { return this._isInitializing; }
  setInitializing(v: boolean) { this._isInitializing = v; }
  getInitializationPromise() { return this._initializationPromise; }
  setInitializationPromise(p: Promise<unknown> | null) { this._initializationPromise = p; }
}

const tokenStore = new TokenStore();

const apiClient = axios.create({
  baseURL: API_BASE,
  timeout: 120000,
  withCredentials: true,
  headers: {
    'Content-Type': 'application/json',
    'User': 'PeakXV-Frontend',
  },
});

// Request interceptor
apiClient.interceptors.request.use(
  async (config: InternalAxiosRequestConfig): Promise<InternalAxiosRequestConfig> => {
    const token = localStorage.getItem('token');
    config.headers = config.headers ?? {};

    // If CSRF is enabled, ensure we have bootstrapped CSRF cookie + token
    // before calling any endpoint other than get-user.
    if (ENABLE_CSRF && !config.url?.includes(GET_USER_PATH)) {
      if (!tokenStore.hasTokens()) {
        await initializeTokens();
      }
    }

    if (ENABLE_OKTA && oktaAuth) {
      try {
        const oktaAccessToken = await oktaAuth.getAccessToken();
        if (oktaAccessToken) {
          config.headers['Authorization'] = `Bearer ${oktaAccessToken}`;
        } else {
          // No token available. Don't redirect during the Okta callback route — the
          // SDK is still in the middle of exchanging the code for tokens. For all
          // other routes, redirect to login.
          const isCallbackRoute =
            typeof window !== 'undefined' &&
            (window.location.pathname.includes('/login/callback') ||
              window.location.pathname.includes('/oauth2/login/callback'));
          if (!isCallbackRoute) {
            await oktaAuth.signInWithRedirect();
            return new Promise(() => {}) as never;
          }
        }
      } catch {
        // Token retrieval failed — only redirect if not on the callback route.
        const isCallbackRoute =
          typeof window !== 'undefined' &&
          (window.location.pathname.includes('/login/callback') ||
            window.location.pathname.includes('/oauth2/login/callback'));
        if (!isCallbackRoute) {
          await oktaAuth.signInWithRedirect();
          return new Promise(() => {}) as never;
        }
      }
    } else if (token) {
      // Optional manual token during local dev.
      config.headers['Authorization'] = `Bearer ${token}`;
    }

    // Add CSRF token to all requests except get-user
    if (ENABLE_CSRF && !config.url?.includes(GET_USER_PATH)) {
      const csrf = tokenStore.getCsrfToken();
      // fastapi-csrf-protect expects X-CSRF-Token
      if (csrf) config.headers['X-CSRF-Token'] = csrf;
    }

    assertSafeOutgoingRequest(config);

    if (!config.skipGlobalLoading) {
      beginApiRequest();
    }

    return config;
  },
  (error: AxiosError) => Promise.reject(error)
);

// Response interceptor to capture CSRF from get-user response
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    if (ENABLE_CSRF && response.config.url?.includes(GET_USER_PATH)) {
      // Extract CSRF token from response body only; browser manages cookies.
      const csrf = response.data?.data?.csrf_token;
      if (csrf) tokenStore.setCsrfToken(csrf);

      tokenStore.markBootstrapped();
      tokenStore.setInitializing(false);
      tokenStore.setInitializationPromise(null);
    }
    return response;
  },
  async (error: AxiosError & { config?: InternalAxiosRequestConfig & { _retry?: boolean } }) => {
    const status = error.response?.status;

    if (status === 401 || status === 403) {
      tokenStore.clear();

      // Avoid recursion on get-user itself
      const cfg = error.config;
      if (cfg && !cfg.url?.includes(GET_USER_PATH) && !cfg._retry) {
        cfg._retry = true;
        try {
          await initializeTokens();
          // Outcome is handed off to a new `apiClient.request`; that request has its own begin/end. Close the
          // 401/403 request's in-flight count here so the global indicator does not get stuck.
          if (!cfg.skipGlobalLoading) {
            endApiRequest();
          }
          return apiClient.request(cfg);
        } catch (e) {
          // Token refresh failed — Okta session is dead. Force re-login instead of
          // leaving the user on a broken UI with silent failures.
          if (ENABLE_OKTA && oktaAuth) {
            await oktaAuth.signInWithRedirect();
            return new Promise(() => {});
          }
          console.error('Failed to refresh tokens:', e);
        }
      }
    }

    // A 500 on get-user almost always means the JWT middleware rejected the request
    // (401 propagated as 500 through Starlette middleware). Force a re-login rather
    // than reloading with the same stale token.
    if (status === 500 && error.config?.url?.includes(GET_USER_PATH)) {
      tokenStore.clear();
      if (ENABLE_OKTA && oktaAuth) {
        await oktaAuth.signInWithRedirect();
      } else {
        window.location.reload();
      }
      return new Promise(() => {}); // suspend - page is redirecting
    }
    // For other 500s that may indicate a stale CSRF token, reload once per session.
    if (status === 500 && !sessionStorage.getItem(CSRF_RELOAD_KEY)) {
      sessionStorage.setItem(CSRF_RELOAD_KEY, '1');
      tokenStore.clear();
      window.location.reload();
      return new Promise(() => {}); // suspend - page is reloading
    }

    return Promise.reject(error);
  }
);

// Pair with `beginApiRequest` in the request interceptor so every completed request decrements.
apiClient.interceptors.response.use(
  (response: AxiosResponse) => {
    const cfg = response.config as InternalAxiosRequestConfig;
    if (!cfg.skipGlobalLoading) {
      endApiRequest();
    }
    return response;
  },
  (error: AxiosError & { config?: InternalAxiosRequestConfig }) => {
    const cfg = error.config;
    if (cfg && !cfg.skipGlobalLoading) {
      endApiRequest();
    }
    return Promise.reject(error);
  }
);

// Initialize tokens: call get-user only when needed; coalesce concurrent calls.
const initializeTokens = async (): Promise<unknown> => {
  if (tokenStore.hasTokens()) return null;

  if (tokenStore.isInitializing() && tokenStore.getInitializationPromise()) {
    return tokenStore.getInitializationPromise();
  }

  tokenStore.setInitializing(true);

  const initPromise = (async () => {
    try {
      const response = await apiClient.get(GET_USER_PATH, { withCredentials: true });
      // Persist user for UI display / local config logs if needed.
      const soha = response.data?.data?.soha_user;
      if (soha) localStorage.setItem('soha_user', typeof soha === 'string' ? soha : JSON.stringify(soha));
      // Response interceptor will set csrf + bootstrapped when CSRF enabled.
      tokenStore.markBootstrapped();
      return response.data;
    } catch (err) {
      tokenStore.setInitializing(false);
      tokenStore.setInitializationPromise(null);
      throw err;
    }
  })();

  tokenStore.setInitializationPromise(initPromise);
  return await initPromise;
};

// Public helpers
export const setCsrfToken = (token: string) => tokenStore.setCsrfToken(token);
export const getCsrfToken = () => tokenStore.getCsrfToken();
export const clearTokens = () => tokenStore.clear();
export const hasTokens = () => tokenStore.hasTokens();

export const refreshTokens = async () => {
  tokenStore.clear();
  return initializeTokens();
};

export const ensureTokensInitialized = async (): Promise<unknown> => {
  return initializeTokens();
};

/** Returns the current bearer token string (without "Bearer " prefix), or null if unavailable. */
export const getAccessToken = async (): Promise<string | null> => {
  if (ENABLE_OKTA && oktaAuth) {
    try {
      const t = await oktaAuth.getAccessToken();
      if (t) return t;
    } catch {
      // Okta session dead — caller should handle null by redirecting to login.
    }
    return null;
  }
  return localStorage.getItem('token');
};

export const debugTokens = () => {
  console.log('=== Token Debug Info ===');
  console.log('CSRF Token:', tokenStore.getCsrfToken());
  console.log('Has Tokens:', tokenStore.hasTokens());
  console.log('Is Initializing:', tokenStore.isInitializing());
  console.log('Last Updated:', new Date(tokenStore.getLastUpdated()).toISOString());
  console.log('========================');
};

export default apiClient;
