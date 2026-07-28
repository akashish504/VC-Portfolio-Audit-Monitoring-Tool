/** Normalize `get-user` / `soha_user` payloads for a short header string. */
export function formatSohaUserDisplay(value: unknown): string {
  if (value == null) return 'User';
  if (typeof value === 'string' && value.trim()) return value.trim();
  if (typeof value === 'object' && value !== null) {
    const o = value as Record<string, unknown>;
    for (const k of ['name', 'user_name', 'preferred_username', 'email'] as const) {
      const v = o[k];
      if (typeof v === 'string' && v.trim()) return v.trim();
    }
  }
  return 'User';
}
