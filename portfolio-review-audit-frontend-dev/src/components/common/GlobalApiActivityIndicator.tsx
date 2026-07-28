import { useSyncExternalStore } from 'react';
import { Loader2 } from 'lucide-react';
import {
  getInFlightRequestCount,
  subscribeInFlightRequestCount,
} from '@/api/globalApiLoadingStore';

/**
 * Small corner panel while any `apiClient` request is in flight, so the user
 * knows the app is working (not only button-level spinners).
 */
export default function GlobalApiActivityIndicator() {
  const inFlight = useSyncExternalStore(
    subscribeInFlightRequestCount,
    getInFlightRequestCount,
    getInFlightRequestCount,
  );

  if (inFlight === 0) return null;

  return (
    <div
      className="pointer-events-none fixed bottom-4 right-4 z-[500] max-w-sm rounded-lg border border-slate-200/90 bg-white/95 px-4 py-3 shadow-lg backdrop-blur-sm dark:border-slate-700 dark:bg-slate-900/95"
      role="status"
      aria-live="polite"
      aria-atomic="true"
    >
      <div className="flex items-center gap-3 text-sm text-slate-800 dark:text-slate-100">
        <Loader2 className="h-5 w-5 shrink-0 animate-spin text-blue-600 dark:text-blue-400" aria-hidden />
        <div className="min-w-0">
          <p className="font-medium leading-tight">Processing…</p>
          <p className="mt-0.5 text-xs text-slate-500 dark:text-slate-400">
            {inFlight > 1
              ? `Waiting for ${inFlight} requests to finish.`
              : 'Please wait for the request to complete.'}
          </p>
        </div>
      </div>
    </div>
  );
}
