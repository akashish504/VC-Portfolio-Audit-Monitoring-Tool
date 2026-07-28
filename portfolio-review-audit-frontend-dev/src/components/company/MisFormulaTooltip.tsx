import * as React from 'react';

import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip';

type Props = {
  metricLabel: string;
  /** Arithmetic formula string from the Snowflake PR mapping config (e.g. "audited_revenue"). */
  formula: string | null | undefined;
  children: React.ReactNode;
};

/** True when the MIS tooltip will render something useful. */
export function misFormulaHasContent(formula?: string | null): boolean {
  return typeof formula === 'string' && formula.trim().length > 0;
}

/**
 * Tooltip shown on hover over a MIS (Snowflake) value cell.
 * Displays the Snowflake PR formula configured for that metric.
 */
export function MisFormulaTooltip({ metricLabel, formula, children }: Props) {
  if (!misFormulaHasContent(formula)) {
    return <>{children}</>;
  }

  // Split formula into identifiers + operators for light syntax highlighting.
  const tokens = (formula as string).trim().split(/(\s+|[+\-*/()])/);

  return (
    <Tooltip delayDuration={150}>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent
        side="right"
        align="start"
        sideOffset={8}
        className="w-[320px] max-w-[90vw] p-0 bg-white text-gray-900 border-gray-200 shadow-xl"
      >
        {/* Header */}
        <div className="px-3 pt-3 pb-2 border-b border-gray-100">
          <div className="text-sm font-semibold text-gray-900">
            {metricLabel} — MIS (Snowflake) formula
          </div>
          <div className="mt-0.5 text-[11px] text-gray-500 leading-tight">
            Derived from PR submission columns
          </div>
        </div>

        {/* Formula */}
        <div className="px-3 py-3">
          <div className="text-[10px] uppercase tracking-wider text-gray-400 mb-1.5 font-medium">
            Formula
          </div>
          <div className="font-mono text-xs bg-gray-50 rounded border border-gray-200 px-2.5 py-2 break-all leading-relaxed">
            {tokens.map((tok, i) => {
              if (/^[+\-*/]$/.test(tok)) {
                return (
                  <span key={i} className="text-blue-600 font-semibold mx-0.5">
                    {tok}
                  </span>
                );
              }
              if (/^\s+$/.test(tok)) return <span key={i}>{tok}</span>;
              if (tok === '(' || tok === ')') {
                return (
                  <span key={i} className="text-gray-400">
                    {tok}
                  </span>
                );
              }
              if (/^\d+(\.\d+)?$/.test(tok)) {
                return (
                  <span key={i} className="text-amber-700">
                    {tok}
                  </span>
                );
              }
              // identifier (column name)
              return (
                <span key={i} className="text-gray-900 font-medium">
                  {tok}
                </span>
              );
            })}
          </div>
        </div>

        <div className="px-3 pb-2.5 text-[10px] text-gray-400 leading-tight">
          Configure formulas in Settings → Snowflake PR Mapping.
        </div>
      </TooltipContent>
    </Tooltip>
  );
}
