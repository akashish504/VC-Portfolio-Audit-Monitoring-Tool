import React, { useEffect } from 'react';
import { Loader2 } from 'lucide-react';

import { useAppState } from '@/context/AppContext';
import { listReviewCycleAudit } from '@/api/audit';
import { SettingsAuditLogPanel } from '@/components/settings/SettingsAuditLogPanel';

const CycleAdjustmentsTab: React.FC = () => {
  const { rcCycles, rcEntries, rcDataLoading, rcDataError, refreshReviewCycleData, ensureReviewCycleDataLoaded } =
    useAppState();

  const busy = rcDataLoading;

  useEffect(() => {
    void ensureReviewCycleDataLoaded();
  }, [ensureReviewCycleDataLoaded]);

  const getCycleLabel = (id: string) => rcCycles.find((c) => c.id === id)?.label ?? id;

  return (
    <div className="relative" aria-busy={busy}>
      {rcDataError && (
        <div className="mb-4 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-800 flex items-center justify-between gap-3">
          <span>{rcDataError}</span>
          <button
            type="button"
            disabled={busy}
            onClick={() => void refreshReviewCycleData()}
            className="shrink-0 px-3 py-1 rounded-md bg-red-100 text-red-900 font-medium hover:bg-red-200 disabled:opacity-50 disabled:pointer-events-none"
          >
            Retry
          </button>
        </div>
      )}

      <div className="mb-4 rounded-lg border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-900">
        Review cycles are provisioned automatically each year on 1 April (once at least two cycles exist in the
        system). Seed cycles and company rosters manually or via CSV; the scheduler appends the next cycle and clones
        company shells from the previous period.
      </div>

      <div className="bg-white rounded-lg border border-gray-200 shadow-sm mb-6">
        <div className="px-4 py-3 border-b border-gray-200">
          <h2 className="text-sm font-semibold text-gray-900">Review Cycles</h2>
        </div>
        <table className="w-full">
          <thead>
            <tr className="bg-gray-50 border-b border-gray-200">
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Cycle</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Companies</th>
              <th className="text-left px-4 py-3 text-xs font-medium text-gray-500 uppercase tracking-wider">Created</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-100">
            {rcDataLoading && (
              <tr>
                <td colSpan={3} className="px-4 py-8 text-center text-sm text-gray-500">
                  <Loader2 className="h-4 w-4 animate-spin inline mr-2" />
                  Loading review cycles…
                </td>
              </tr>
            )}
            {!rcDataLoading &&
              rcCycles.map((cycle) => (
                <tr key={cycle.id} className="hover:bg-gray-50 transition-colors">
                  <td className="px-4 py-3 text-sm font-medium text-gray-900">{cycle.label}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">{rcEntries.filter((e) => e.reviewCycleId === cycle.id).length}</td>
                  <td className="px-4 py-3 text-sm text-gray-500">{new Date(cycle.createdAt).toLocaleDateString()}</td>
                </tr>
              ))}
            {!rcDataLoading && rcCycles.length === 0 && (
              <tr>
                <td colSpan={3} className="px-4 py-8 text-center text-sm text-gray-400">
                  No review cycles yet — seed at least two cycles to enable the annual scheduler.
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>

      <SettingsAuditLogPanel
        title="Cycle adjustment logs"
        fetchLogs={listReviewCycleAudit}
        searchable

        getReviewCycleLabel={getCycleLabel}
      />
    </div>
  );
};

export default CycleAdjustmentsTab;
