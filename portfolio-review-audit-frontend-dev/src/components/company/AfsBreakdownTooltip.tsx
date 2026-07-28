import * as React from 'react';
import { ScanSearch } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type {
  MappingBreakdown,
  MappingBreakdownTerm,
} from '@/api/portfolio';
import type { ApiFinancialMetricTerm } from '@/api/settings';
import { cn } from '@/lib/utils';

import { fmtAmount } from './DiscrepancyDashboard';

/** True when the AFS tooltip will render something useful for the given inputs. */
export function afsBreakdownHasContent(
  breakdown?: MappingBreakdown | null,
  fallbackTerms?: ApiFinancialMetricTerm[] | null,
): boolean {
  if (breakdown && Array.isArray(breakdown.terms) && breakdown.terms.length > 0) return true;
  if (fallbackTerms && fallbackTerms.length > 0) return true;
  return false;
}

type SourceRef = {
  page: number;
  text_snippet: string;
  /** Backend verify-pass confidence: "verified" (exact), "inherited" (approx), "unverified" (no link). */
  source?: 'verified' | 'inherited' | 'unverified';
};

type Props = {
  metricLabel: string;
  breakdown?: MappingBreakdown | null;
  /** Used only when `breakdown` is absent (e.g. row hasn't been re-synced yet). */
  fallbackTerms?: ApiFinancialMetricTerm[] | null;
  currency?: string | null;
  /** Per-path source refs for the entity's AFS file. When a term's path has one, its path
   *  becomes a clickable link that opens the PDF source viewer. Optional — omit to keep
   *  the tooltip exactly as before (no click affordance). */
  sourceRefs?: Record<string, SourceRef> | null;
  /** Open the PDF source viewer for a dotted path. */
  onShowSource?: (path: string) => void;
  /** True when the entity has an AFS PDF — lets in-OCR terms be clickable even without a precise ref. */
  fileAvailable?: boolean;
  /** Which side the tooltip opens to. Defaults to "left" (right-aligned dashboard cells); pass
   *  "right" where the trigger sits on the left of the screen (e.g. the file-tagging tree) so the
   *  panel doesn't clip off the left edge. */
  side?: 'left' | 'right' | 'top' | 'bottom';
  children: React.ReactNode;
};

function splitPath(path: string): { leaf: string; full: string } {
  const segments = (path || '').split('.').filter(Boolean);
  const leaf = segments[segments.length - 1] ?? path;
  return { leaf: humanizeSegment(leaf), full: path };
}

function humanizeSegment(seg: string): string {
  return (seg || '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

function SignChip({ sign }: { sign: '+' | '-' }) {
  return (
    <span
      className={cn(
        'inline-flex h-5 w-5 items-center justify-center rounded-full text-xs font-semibold shrink-0',
        sign === '+'
          ? 'bg-emerald-100 text-emerald-700'
          : 'bg-rose-100 text-rose-700',
      )}
      aria-label={sign === '+' ? 'added' : 'subtracted'}
    >
      {sign === '+' ? '+' : '−'}
    </span>
  );
}

function TermRow({
  term,
  currency,
  sourceRef,
  onShowSource,
  fileAvailable,
}: {
  term: MappingBreakdownTerm;
  currency?: string | null;
  sourceRef?: SourceRef | null;
  onShowSource?: (path: string) => void;
  /** The entity has an AFS PDF, so the source viewer can be opened (page 1 when no precise ref). */
  fileAvailable?: boolean;
}) {
  const { leaf, full } = splitPath(term.path);
  const isMissing = term.source === 'default_zero';
  const valueLabel = isMissing ? '0' : fmtAmount(term.raw_value, currency);
  // Link only to a deterministically located page. `verified` = exact (located in the PDF). Records
  // with no `source` are legacy (pre-locator) and stay clickable so existing files don't regress —
  // the backfill upgrades them. `inherited`/`unverified` are NOT clickable (no guessed pages).
  const refSource = sourceRef?.source;
  const usable = !refSource || refSource === 'verified';
  const linkable = !!onShowSource && !isMissing && !!fileAvailable && !!sourceRef && usable;
  return (
    <div className="flex items-start gap-2 py-1.5">
      <SignChip sign={term.sign} />
      <div className="min-w-0 flex-1">
        {linkable ? (
          <button
            type="button"
            onClick={() => onShowSource!(term.path)}
            title={`View source in PDF — ${full}`}
            className="flex w-full items-start gap-1.5 text-left text-sm font-medium text-indigo-600 hover:text-indigo-800"
          >
            <span className="break-words underline decoration-dotted underline-offset-2">{leaf}</span>
            <ScanSearch className="h-3 w-3 shrink-0 mt-[3px] text-indigo-400" aria-hidden />
          </button>
        ) : (
          <>
            <div className="text-left text-sm font-medium text-gray-800 break-words">{leaf}</div>
            <div className="text-left text-[10px] text-gray-500 break-all leading-tight">{full}</div>
          </>
        )}
        {isMissing && (
          <div className="text-left text-[10px] text-amber-600 mt-0.5">not in OCR — defaulted to 0</div>
        )}
      </div>
      <div className="text-right font-mono text-xs text-gray-800 shrink-0 tabular-nums">
        {valueLabel}
      </div>
    </div>
  );
}

function FallbackTermRow({ term }: { term: ApiFinancialMetricTerm }) {
  const { leaf, full } = splitPath(term.path);
  return (
    <div className="flex items-start gap-2 py-1.5">
      <SignChip sign={term.sign} />
      <div className="min-w-0 flex-1">
        <div className="text-left text-sm font-medium text-gray-800 break-words">{leaf}</div>
        <div className="text-left text-[10px] text-gray-500 break-all leading-tight">{full}</div>
      </div>
      <div className="text-right font-mono text-xs text-gray-400 shrink-0">—</div>
    </div>
  );
}

function formatRelativeTime(iso?: string | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  const deltaSec = Math.max(0, Math.floor((Date.now() - t) / 1000));
  if (deltaSec < 60) return 'just now';
  if (deltaSec < 3600) return `${Math.floor(deltaSec / 60)}m ago`;
  if (deltaSec < 86400) return `${Math.floor(deltaSec / 3600)}h ago`;
  return `${Math.floor(deltaSec / 86400)}d ago`;
}

/**
 * Tooltip shown on hover over an AFS value cell.
 * Displays the OCR extraction mapping terms (paths, signs, values) that produced the AFS amount.
 */
export function AfsBreakdownTooltip({ metricLabel, breakdown, fallbackTerms, currency, sourceRefs, onShowSource, fileAvailable, side = 'left', children }: Props) {
  const effectiveCurrency = breakdown?.currency || currency || null;
  const hasBreakdown = !!breakdown && Array.isArray(breakdown.terms) && breakdown.terms.length > 0;
  const hasFallback = !!fallbackTerms && fallbackTerms.length > 0;

  if (!hasBreakdown && !hasFallback) {
    return <>{children}</>;
  }

  const computedAtLabel = formatRelativeTime(breakdown?.computed_at);

  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent
        side={side}
        align="start"
        sideOffset={8}
        collisionPadding={8}
        className="w-[360px] max-w-[90vw] p-0 bg-white text-gray-900 border-gray-200 shadow-xl"
      >
        <div className="px-3 pt-3 pb-2 border-b border-gray-100">
          <div className="flex items-baseline justify-between gap-2">
            <div className="text-sm font-semibold text-gray-900">
              {metricLabel} — AFS extraction
            </div>
            {effectiveCurrency && (
              <div className="text-[10px] uppercase tracking-wider text-gray-500">
                {effectiveCurrency}
              </div>
            )}
          </div>
          {!hasBreakdown && hasFallback && (
            <div className="mt-1 text-[11px] text-amber-700 leading-tight">
              Values not yet computed for this row. Showing configured terms only.
            </div>
          )}
        </div>

        <div className="px-3 py-1.5 max-h-[60vh] overflow-y-auto divide-y divide-gray-100">
          {hasBreakdown
            ? breakdown!.terms.map((t, i) => (
                <TermRow
                  key={`${t.path}-${i}`}
                  term={t}
                  currency={effectiveCurrency}
                  sourceRef={sourceRefs?.[t.path] ?? null}
                  onShowSource={onShowSource}
                  fileAvailable={fileAvailable}
                />
              ))
            : fallbackTerms!.map((t, i) => (
                <FallbackTermRow key={`${t.path}-${i}`} term={t} />
              ))}
        </div>

        {hasBreakdown && (
          <div className="px-3 py-2 border-t border-gray-100 flex items-center justify-between bg-gray-50">
            <span className="text-[11px] font-semibold text-gray-600 uppercase tracking-wider">
              Total
            </span>
            <span className="font-mono text-sm font-semibold text-gray-900 tabular-nums">
              {fmtAmount(breakdown!.total, effectiveCurrency)}
            </span>
          </div>
        )}

        {computedAtLabel && (
          <div className="px-3 pb-2 text-[10px] text-gray-400 text-right">
            computed {computedAtLabel}
          </div>
        )}
      </TooltipContent>
    </Tooltip>
  );
}
