import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useOktaAuth } from '@okta/okta-react';
import { SidebarProvider, SidebarTrigger } from '@/components/ui/sidebar';
import { AppSidebar } from '@/components/AppSidebar';
import { Loader2, RefreshCw } from 'lucide-react';
import { toast } from 'sonner';
import { ENABLE_OKTA } from '@/api/config';
import { formatSohaUserDisplay } from '@/utils/userDisplay';
import { dataService } from '@/api/services/dataService';
import { userService } from '@/api/services/userService';
import RouteTransitionLoader from '@/components/common/RouteTransitionLoader';

interface AppLayoutProps {
  children: React.ReactNode;
}

const signOutButtonClass =
  'inline-flex items-center px-3 py-1 rounded-md text-xs font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 transition-colors';

function OktaHeaderAuthButtons() {
  const { oktaAuth, authState } = useOktaAuth();
  if (authState?.isAuthenticated) {
    return (
      <button
        type="button"
        onClick={() => void oktaAuth.signOut()}
        className={signOutButtonClass}
      >
        Sign out
      </button>
    );
  }
  return (
    <button
      type="button"
      onClick={() => void oktaAuth.signInWithRedirect()}
      className="inline-flex items-center px-3 py-1 rounded-md text-xs font-semibold text-white bg-blue-600 hover:bg-blue-700 transition-colors"
    >
      Sign in
    </button>
  );
}

function SignOutDisabledLocal() {
  return (
    <button
      type="button"
      disabled
      className="inline-flex items-center px-3 py-1 rounded-md text-xs font-medium text-gray-400 bg-gray-100 border border-gray-200 cursor-not-allowed opacity-80"
      title="Okta and CSRF are off in local dev. In deployment, set VITE_ENABLE_OKTA and VITE_ENABLE_CSRF to enable Sign out."
    >
      Sign out
    </button>
  );
}

const POLL_MS = 30_000;

const AppLayout = ({ children }: AppLayoutProps) => {
  const [userLabel, setUserLabel] = useState('User');
  const [batchStatus, setBatchStatus] = useState<string | undefined>(undefined);
  const pollingRef = useRef<number | null>(null);
  const awaitingSyncToast = useRef(false);

  useEffect(() => {
    const stored = localStorage.getItem('soha_user');
    if (stored) {
      try {
        setUserLabel(formatSohaUserDisplay(JSON.parse(stored)));
      } catch {
        setUserLabel('Anonymous');
      }
    } else {
      setUserLabel('User');
    }

    const storedBatch = localStorage.getItem('batch_process_status') || undefined;
    setBatchStatus(storedBatch ?? undefined);

    const onAuth = (e: Event) => {
      const d = (e as CustomEvent<{ soha_user?: unknown; batch_process_status?: string }>).detail;
      if (d?.soha_user !== undefined) {
        setUserLabel(formatSohaUserDisplay(d.soha_user));
      }
      if (d?.batch_process_status !== undefined) {
        setBatchStatus(d.batch_process_status);
      }
    };
    window.addEventListener('auth:init', onAuth);
    return () => window.removeEventListener('auth:init', onAuth);
  }, []);

  const isBatchFailed = useMemo(() => batchStatus === 'FAILED', [batchStatus]);
  const isBatchInProgress = useMemo(() => batchStatus === 'IN_PROGRESS', [batchStatus]);
  const isBlockingSync = useMemo(
    () => batchStatus === 'QUEUED' || batchStatus === 'IN_PROGRESS',
    [batchStatus]
  );

  const checkStatus = useCallback(async () => {
    try {
      const res = await userService.getUser();
      const status = res?.data?.batch_process_status as string | undefined;
      if (status) {
        setBatchStatus(status);
        localStorage.setItem('batch_process_status', status);
        window.dispatchEvent(
          new CustomEvent('auth:init', { detail: { batch_process_status: status } })
        );
        if (status === 'SUCCESS' && awaitingSyncToast.current) {
          toast.success('Data synced successfully');
          awaitingSyncToast.current = false;
        }
        if (status === 'SUCCESS' || status === 'FAILED') {
          if (pollingRef.current) {
            clearInterval(pollingRef.current);
            pollingRef.current = null;
          }
        }
      }
    } catch (err) {
      console.error('Polling get-user failed', err);
    }
  }, []);

  const startBatchStatusPolling = useCallback(() => {
    if (pollingRef.current) {
      clearInterval(pollingRef.current);
      pollingRef.current = null;
    }
    setBatchStatus('IN_PROGRESS');
    localStorage.setItem('batch_process_status', 'IN_PROGRESS');
    window.dispatchEvent(
      new CustomEvent('auth:init', { detail: { batch_process_status: 'IN_PROGRESS' } })
    );
    void checkStatus();
    pollingRef.current = window.setInterval(() => void checkStatus(), POLL_MS);
  }, [checkStatus]);

  useEffect(() => {
    if (batchStatus && batchStatus !== 'SUCCESS' && batchStatus !== 'FAILED') {
      if (!pollingRef.current) {
        startBatchStatusPolling();
      }
    } else if (batchStatus === 'SUCCESS' || batchStatus === 'FAILED') {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    }
  }, [batchStatus, startBatchStatusPolling]);

  useEffect(() => {
    return () => {
      if (pollingRef.current) {
        clearInterval(pollingRef.current);
        pollingRef.current = null;
      }
    };
  }, []);

  const handleSyncData = async () => {
    try {
      const response = await dataService.syncData();
      if (response.success === false) {
        toast.error(response.message || 'Could not start data sync.');
        return;
      }
      awaitingSyncToast.current = true;
      startBatchStatusPolling();
    } catch (e) {
      console.error(e);
      toast.error('Data sync request failed.');
    }
  };

  const syncDisabled = isBlockingSync || isBatchFailed;

  return (
    <SidebarProvider>
      <div className="min-h-screen flex w-full relative">
        {isBlockingSync && (
          <div className="fixed inset-0 z-[200] bg-white/60 backdrop-blur-sm flex items-center justify-center pointer-events-auto">
            <div className="flex flex-col items-center gap-3 max-w-md text-center px-4">
              <Loader2 className="h-8 w-8 animate-spin text-blue-600" />
              <p className="text-sm font-medium text-gray-700">
                Data sync in progress. The application is temporarily read-only until sync completes.
              </p>
            </div>
          </div>
        )}

        <AppSidebar />
        <div className="flex-1 flex flex-col min-w-0 relative">
          <header className="h-10 flex items-center justify-between border-b border-gray-200 bg-white px-2 shrink-0 z-10">
            <SidebarTrigger className="text-gray-400 hover:text-gray-600" />

            <div className="flex items-center gap-2 pr-2">
              <button
                onClick={() => void handleSyncData()}
                disabled={syncDisabled}
                className="inline-flex items-center gap-1.5 px-3 py-1 bg-blue-600 text-white text-xs font-semibold rounded-md hover:bg-blue-700 disabled:opacity-70 disabled:cursor-not-allowed transition-colors"
              >
                {isBatchInProgress || batchStatus === 'QUEUED' ? (
                  <Loader2 className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <RefreshCw className="h-3.5 w-3.5" />
                )}
                {isBatchInProgress || batchStatus === 'QUEUED' ? 'Syncing...' : 'Sync Data'}
              </button>

              <span className="text-sm text-gray-700">Hello, {userLabel}</span>
              {ENABLE_OKTA ? <OktaHeaderAuthButtons /> : <SignOutDisabledLocal />}
            </div>
          </header>

          <main
            className={`relative flex-1 overflow-auto bg-gray-50 ${
              isBatchFailed ? 'pointer-events-none opacity-50' : isBlockingSync ? 'pointer-events-none' : ''
            }`}
          >
            {children}
            <RouteTransitionLoader />
          </main>

          {isBatchFailed && (
            <div className="fixed inset-0 z-[110] flex items-center justify-center bg-black/50 p-4">
              <div className="bg-white rounded-lg shadow-lg p-6 max-w-md w-full border-l-4 border-red-500">
                <div className="flex items-center mb-3">
                  <div className="w-3 h-3 bg-red-500 rounded-full mr-3" />
                  <h2 className="text-lg font-semibold text-red-700">Data sync failed</h2>
                </div>
                <p className="text-sm text-gray-600">
                  The last data sync failed. Please contact support or try again later. The application stays disabled
                  until the issue is resolved or a new sync succeeds.
                </p>
              </div>
            </div>
          )}
        </div>
      </div>
    </SidebarProvider>
  );
};

export default AppLayout;
