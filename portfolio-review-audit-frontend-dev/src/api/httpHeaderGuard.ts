/**
 * Mitigation for CRLF / header-injection gadget chains (CWE-113): reject values that could
 * split HTTP framing if merged into headers via prototype pollution or other merge bugs.
 */
import type { InternalAxiosRequestConfig } from 'axios';
import { AxiosHeaders } from 'axios';

const UNSAFE = /[\r\n\u0000]/;

function assertSafeString(label: string, value: string): void {
  if (UNSAFE.test(value)) {
    throw new Error(`Blocked HTTP request: ${label} contains CR, LF, or NUL`);
  }
}

/**
 * Run after all request interceptors have set headers. Validates final header names/values,
 * plus URL pieces axios will concatenate.
 */
export function assertSafeOutgoingRequest(config: InternalAxiosRequestConfig): void {
  if (config.baseURL) assertSafeString('baseURL', String(config.baseURL));
  if (config.url) assertSafeString('url', String(config.url));

  if (!config.headers) return;

  const merged = AxiosHeaders.from(config.headers);
  const flat = merged.toJSON(true) as Record<string, string | string[] | undefined>;

  for (const [name, raw] of Object.entries(flat)) {
    assertSafeString('header name', name);
    if (raw == null || raw === false) continue;
    const parts = Array.isArray(raw) ? raw : [raw];
    for (const p of parts) {
      assertSafeString(`header ${name}`, String(p));
    }
  }
}
