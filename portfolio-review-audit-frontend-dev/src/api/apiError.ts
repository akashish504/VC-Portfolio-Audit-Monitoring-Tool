import axios from 'axios';

/** Reads FastAPI `detail` (string or validation list) from an Axios error response. */
export function getApiErrorMessage(error: unknown, fallback = 'Request failed'): string {
  if (axios.isAxiosError(error)) {
    const data = error.response?.data as { detail?: unknown } | undefined;
    if (data?.detail != null) {
      if (typeof data.detail === 'string') return data.detail;
      if (Array.isArray(data.detail)) {
        const parts = data.detail.map((item: unknown) => {
          if (item && typeof item === 'object' && 'msg' in item) {
            return String((item as { msg: string }).msg);
          }
          return JSON.stringify(item);
        });
        const joined = parts.filter(Boolean).join('; ');
        if (joined) return joined;
      }
    }
    if (error.message) return error.message;
  }
  if (error instanceof Error) return error.message;
  return fallback;
}
