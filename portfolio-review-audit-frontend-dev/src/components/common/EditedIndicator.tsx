import * as React from 'react';
import { Pencil } from 'lucide-react';

import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from '@/components/ui/tooltip';
import { cn } from '@/lib/utils';
import type { ManualEditMarker } from '@/api/portfolio';

export type { ManualEditMarker };

/** True when a marker represents an actual manual edit worth flagging. */
function isManualEdit(marker?: ManualEditMarker | null): marker is ManualEditMarker {
  return !!marker && marker.edited !== false;
}

function formatWhen(iso?: string | null): string | null {
  if (!iso) return null;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return null;
  return new Date(t).toLocaleString();
}

function formatVal(v: ManualEditMarker['previous_value']): string {
  if (v === null || v === undefined) return '—';
  if (typeof v === 'number') return v.toLocaleString(undefined, { maximumFractionDigits: 4 });
  return String(v);
}

/**
 * Amber "Manually edited" badge + hover panel with who / when / why / previous→new value.
 * Renders nothing when there is no manual-edit marker, so it is safe to drop next to any value.
 *
 * Wraps its own TooltipProvider (the file/dashboard pages have no global one), matching the
 * pattern used by AfsBreakdownTooltip.
 */
export function EditedIndicator({
  marker,
  className,
  label = 'Edited',
}: {
  marker?: ManualEditMarker | null;
  className?: string;
  /** Short badge text. Defaults to "Edited". */
  label?: string;
}) {
  if (!isManualEdit(marker)) return null;

  const when = formatWhen(marker.edited_at);
  const who = (marker.edited_by || '').trim();
  const reason = (marker.reason || '').trim();
  const hasPrevNew = marker.previous_value !== undefined || marker.new_value !== undefined;

  return (
    <TooltipProvider delayDuration={100}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            className={cn(
              'inline-flex items-center gap-1 rounded-full border border-amber-300 bg-amber-100 px-1.5 py-0.5',
              'text-[10px] font-medium text-amber-700 cursor-help select-none align-middle',
              className,
            )}
            aria-label="Manually edited"
          >
            <Pencil className="h-2.5 w-2.5" aria-hidden />
            {label}
          </span>
        </TooltipTrigger>
        <TooltipContent
          side="top"
          align="start"
          sideOffset={6}
          collisionPadding={8}
          className="max-w-xs w-64 bg-white text-gray-900 border-gray-200 shadow-xl p-0"
        >
          <div className="border-b border-gray-100 px-3 py-2">
            <div className="flex items-center gap-1.5 text-sm font-semibold text-amber-700">
              <Pencil className="h-3.5 w-3.5" aria-hidden />
              Manually edited
            </div>
            <p className="mt-0.5 text-[11px] leading-snug text-gray-500">
              This value was changed by a reviewer — it is not the originally extracted figure.
            </p>
          </div>
          <div className="space-y-1.5 px-3 py-2 text-xs">
            {who && (
              <div className="flex justify-between gap-3">
                <span className="text-gray-500">By</span>
                <span className="font-medium text-gray-800 break-all text-right">{who}</span>
              </div>
            )}
            {when && (
              <div className="flex justify-between gap-3">
                <span className="text-gray-500">When</span>
                <span className="font-medium text-gray-800 text-right">{when}</span>
              </div>
            )}
            {hasPrevNew && (
              <div className="flex justify-between gap-3">
                <span className="text-gray-500">Changed</span>
                <span className="font-mono text-gray-800 text-right tabular-nums">
                  {formatVal(marker.previous_value)} → {formatVal(marker.new_value)}
                </span>
              </div>
            )}
            <div className="pt-1">
              <span className="text-gray-500">Justification</span>
              {reason ? (
                <p className="mt-0.5 rounded bg-gray-50 px-2 py-1 italic text-gray-700">{reason}</p>
              ) : (
                <p className="mt-0.5 text-gray-400 italic">No justification provided.</p>
              )}
            </div>
          </div>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  );
}
