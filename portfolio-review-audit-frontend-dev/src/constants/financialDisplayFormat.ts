/**
 * Display-only formatting for currency financials (no FX conversion).
 * INR → lakhs crore scale (/ 1e7), USD → millions (/ 1e6), other → full locale amount.
 */

function fullNumberLocalized(value: number, fractionDigits = 2): string {
  return value.toLocaleString(undefined, {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

/** Human-readable monetary amount from stored numeric + ISO code. */
export function formatFinancialAmount(isoRaw: string | null | undefined, value: number): string {
  const iso = (isoRaw || '').trim().toUpperCase();
  if (iso === 'INR') return `${fullNumberLocalized(value / 1e7, 2)} INR Cr`;
  if (iso === 'USD') return `$${fullNumberLocalized(value / 1e6, 2)} million`;
  const body = fullNumberLocalized(value, 2);
  return iso ? `${iso} ${body}` : body;
}

/**
 * Absolute |MIS − AFS| — same scaling rules when MIS and AFS share the row currency basis;
 * if currencies mismatch, show raw localized difference without scaling.
 */
export function formatAbsoluteVarianceAmount(
  absDiff: number,
  opts: {
    misCurrency: string | null | undefined;
    afsCurrency: string | null | undefined;
  },
): string {
  const mc = opts.misCurrency?.trim().toUpperCase();
  const ac = opts.afsCurrency?.trim().toUpperCase();
  if (mc && mc === ac) return formatFinancialAmount(mc, absDiff);
  return fullNumberLocalized(absDiff, 2);
}
