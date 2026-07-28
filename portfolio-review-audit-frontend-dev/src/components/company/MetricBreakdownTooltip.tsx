import * as React from 'react';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';
import type {
  FinancialMetricKey,
  MappingBreakdown,
  MappingBreakdownDerived,
  MappingBreakdownTerm,
} from '@/api/portfolio';
import type { ApiFinancialMetricTerm } from '@/api/settings';
import { cn } from '@/lib/utils';

import { fmtAmount } from './DiscrepancyDashboard';

/** True when the tooltip will render something useful for the given inputs. */
export function metricBreakdownHasContent(
  breakdown?: MappingBreakdown | null,
  fallbackTerms?: ApiFinancialMetricTerm[] | null,
): boolean {
  if (breakdown && Array.isArray(breakdown.terms) && breakdown.terms.length > 0) return true;
  if (fallbackTerms && fallbackTerms.length > 0) return true;
  return false;
}

type Props = {
  metricKey: FinancialMetricKey;
  metricLabel: string;
  breakdown?: MappingBreakdown | null;
  /** Used only when `breakdown` is absent (e.g. row hasn't been re-synced yet). */
  fallbackTerms?: ApiFinancialMetricTerm[] | null;
  currency?: string | null;
  children: React.ReactNode;
};

/** Split a dotted path into ``[leafLabel, fullPath]`` for two-line rendering. */
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
}: {
  term: MappingBreakdownTerm;
  currency?: string | null;
}) {
  const { leaf, full } = splitPath(term.path);
  const isMissing = term.source === 'default_zero';
  const valueLabel = isMissing ? '0' : fmtAmount(term.raw_value, currency);
  return (
    <div className="flex items-start gap-2 py-1.5">
      <SignChip sign={term.sign} />
      <div className="min-w-0 flex-1">
        <div className="text-sm font-medium text-gray-800 truncate">{leaf}</div>
        <div className="text-[10px] text-gray-500 break-all leading-tight">{full}</div>
        {isMissing && (
          <div className="text-[10px] text-amber-600 mt-0.5">not in OCR — defaulted to 0</div>
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
        <div className="text-sm font-medium text-gray-800 truncate">{leaf}</div>
        <div className="text-[10px] text-gray-500 break-all leading-tight">{full}</div>
      </div>
      <div className="text-right font-mono text-xs text-gray-400 shrink-0">—</div>
    </div>
  );
}

function DerivedComponentsBlock({
  derived,
  currency,
}: {
  derived: MappingBreakdownDerived;
  currency?: string | null;
}) {
  const entries = Object.entries(derived.components || {});
  if (entries.length === 0) return null;
  return (
    <div className="mt-2 rounded-md border border-gray-200 bg-gray-50 px-2 py-1.5">
      <div className="text-[11px] font-semibold text-gray-700">
        Derived components
      </div>
      <div className="text-[10px] text-gray-500 mb-1 font-mono">{derived.formula}</div>
      <div className="space-y-1">
        {entries.map(([name, comp]) => (
          <div key={name} className="flex items-start gap-2 text-[11px]">
            <span className="font-medium text-gray-700 capitalize w-24 shrink-0">
              {name.replace(/_/g, ' ')}
            </span>
            <span className="text-gray-500 break-all flex-1 leading-tight">
              {comp.path ?? <span className="italic">no path</span>}
            </span>
            <span className="font-mono tabular-nums text-gray-800 shrink-0">
              {comp.value == null ? (
                <span className="italic text-amber-600">missing</span>
              ) : (
                fmtAmount(comp.value, currency)
              )}
            </span>
          </div>
        ))}
      </div>
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

export function MetricBreakdownTooltip({
  metricKey,
  metricLabel,
  breakdown,
  fallbackTerms,
  currency,
  children,
}: Props) {
  const effectiveCurrency = breakdown?.currency || currency || null;
  const hasBreakdown = !!breakdown && Array.isArray(breakdown.terms) && breakdown.terms.length > 0;
  const hasFallback = !!fallbackTerms && fallbackTerms.length > 0;

  // Nothing to show — render the trigger plain (no tooltip) so we don't open empty popovers.
  if (!hasBreakdown && !hasFallback) {
    return <>{children}</>;
  }

  const derivedForMetric: MappingBreakdownDerived | undefined =
    breakdown?.derived_components?.[metricKey] ?? undefined;

  const computedAtLabel = formatRelativeTime(breakdown?.computed_at);

  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-[360px] max-w-[90vw] p-0 bg-white text-gray-900 border-gray-200 shadow-xl"
      >
        <div className="px-3 pt-3 pb-2 border-b border-gray-100">
          <div className="flex items-baseline justify-between gap-2">
            <div className="text-sm font-semibold text-gray-900">
              {metricLabel} formula breakdown
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
                <TermRow key={`${t.path}-${i}`} term={t} currency={effectiveCurrency} />
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

        {derivedForMetric && (
          <div className="px-3 pb-3">
            <DerivedComponentsBlock derived={derivedForMetric} currency={effectiveCurrency} />
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
