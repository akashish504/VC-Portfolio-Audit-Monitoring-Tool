import { lazy, Suspense, useEffect, type ReactNode } from 'react';
import { BrowserRouter, Routes, Route, Outlet } from 'react-router-dom';
import { Security, LoginCallback, useOktaAuth } from '@okta/okta-react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { oktaAuth } from './auth/okta';
import AppLayout from './components/layout/AppLayout';
import LoadingSpinner from './components/common/LoadingSpinner';
import GlobalApiActivityIndicator from './components/common/GlobalApiActivityIndicator';
import { ensureTokensInitialized, hasTokens } from './api/axios';
import { AppProvider } from './context/AppContext';
import { Toaster } from './components/ui/sonner';
import { ENABLE_OKTA } from './api/config';

const PortfolioCompaniesPage = lazy(() => import('./pages/PortfolioCompaniesPage'));
const CompanyDetailPage = lazy(() => import('./pages/CompanyDetailPage'));
const AuditTrackerPage = lazy(() => import('./pages/AuditTrackerPage'));
const ScopingDashboardPage = lazy(() => import('./pages/ScopingDashboardPage'));
const VarianceDashboardPage = lazy(() => import('./pages/VarianceDashboardPage'));
const TimelineDashboardPage = lazy(() => import('./pages/TimelineDashboardPage'));
const EmailTemplatesPage = lazy(() => import('./pages/EmailTemplatesPage'));
const EmailThreadsPage = lazy(() => import('./pages/EmailThreadsPage'));
const FileTaggingPage = lazy(() => import('./pages/FileTaggingPage'));
const FileDetailPage = lazy(() => import('./pages/FileDetailPage'));
const EmailTaggingPage = lazy(() => import('./pages/EmailTaggingPage'));
const ParameterThresholdPage = lazy(() => import('./pages/ParameterThresholdPage'));
const SettingsPage = lazy(() => import('./pages/SettingsPage'));
const OrgChartPage = lazy(() => import('./pages/OrgChartPage'));
const AuditPipelinePage = lazy(() => import('./pages/AuditPipelinePage'));
const ComparisonWorkspacePage = lazy(() => import('./pages/ComparisonWorkspacePage'));
const CommunicationsPage = lazy(() => import('./pages/CommunicationsPage'));
const MasterScopingPage = lazy(() => import('./pages/MasterScopingPage'));
const AuditedFinancialsEmailsPage = lazy(() => import('./pages/AuditedFinancialsEmailsPage'));
const SyncAlertsPage = lazy(() => import('./pages/SyncAlertsPage'));
const NotFound = lazy(() => import('./pages/NotFound'));

const LAZY_PAGES: { path: string; el: ReactNode }[] = [
  { path: '/', el: <PortfolioCompaniesPage /> },
  { path: '/company/:companyId', el: <CompanyDetailPage /> },
  { path: '/review-cycle-adjustments', el: <AuditTrackerPage /> },
  { path: '/audit-dashboard', el: <TimelineDashboardPage /> },
  { path: '/audit-dashboard/scoping', el: <ScopingDashboardPage /> },
  { path: '/audit-dashboard/variance', el: <VarianceDashboardPage /> },
  { path: '/audit-dashboard/timeline', el: <TimelineDashboardPage /> },
  { path: '/email-templates', el: <EmailTemplatesPage /> },
  { path: '/email-threads', el: <EmailThreadsPage /> },
  { path: '/file-tagging', el: <FileTaggingPage /> },
  { path: '/file-tagging/:fileId', el: <FileDetailPage /> },
  { path: '/email-tagging', el: <EmailTaggingPage /> },
  { path: '/audited-financials-emails', el: <AuditedFinancialsEmailsPage /> },
  { path: '/parameter-threshold', el: <ParameterThresholdPage /> },
  { path: '/settings', el: <SettingsPage /> },
  { path: '/org-chart', el: <OrgChartPage /> },
  { path: '/pipeline', el: <AuditPipelinePage /> },
  { path: '/workspace', el: <ComparisonWorkspacePage /> },
  { path: '/communications', el: <CommunicationsPage /> },
  { path: '/master-scoping', el: <MasterScopingPage /> },
  { path: '/sync-alerts', el: <SyncAlertsPage /> },
  { path: '*', el: <NotFound /> },
];

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: false } },
});

/** Okta gate: layout (and header user menu) stay outside this; only page content is gated. */
function RequireAuth() {
  const { oktaAuth, authState } = useOktaAuth();

  useEffect(() => {
    if (authState && authState.isAuthenticated === false) {
      oktaAuth.signInWithRedirect();
    }
  }, [authState, oktaAuth]);

  if (!authState || authState.isAuthenticated === undefined) {
    return (
      <div className="flex min-h-[40vh] w-full items-center justify-center p-6">
        <LoadingSpinner />
      </div>
    );
  }

  if (!authState.isAuthenticated) {
    return null;
  }

  return <Outlet />;
}

function PassThrough() {
  return <Outlet />;
}

function LocalAuthBootstrapper() {
  useEffect(() => {
    const initialize = async () => {
      try {
        if (!hasTokens()) {
          const initData = (await ensureTokensInitialized()) as {
            data?: { soha_user?: unknown; batch_process_status?: string };
          } | null;
          const sohaUser = initData?.data?.soha_user;
          const batchStatus = initData?.data?.batch_process_status as string | undefined;
          if (sohaUser) {
            localStorage.setItem('soha_user', JSON.stringify(sohaUser));
          }
          if (batchStatus) {
            localStorage.setItem('batch_process_status', batchStatus);
          }
          if (sohaUser || batchStatus) {
            window.dispatchEvent(
              new CustomEvent('auth:init', { detail: { soha_user: sohaUser, batch_process_status: batchStatus } })
            );
          }
        }
      } catch (error) {
        console.error('Init failed, but continuing app load:', error);
      }
    };

    void initialize();
  }, []);
  return null;
}

function OktaAuthBootstrapper() {
  const { authState } = useOktaAuth();

  useEffect(() => {
    if (authState?.isAuthenticated !== true) return;
    const initialize = async () => {
      try {
        if (!hasTokens()) {
          const initData = (await ensureTokensInitialized()) as {
            data?: { soha_user?: unknown; batch_process_status?: string };
          } | null;
          const sohaUser = initData?.data?.soha_user;
          const batchStatus = initData?.data?.batch_process_status as string | undefined;
          if (sohaUser) {
            localStorage.setItem('soha_user', JSON.stringify(sohaUser));
          }
          if (batchStatus) {
            localStorage.setItem('batch_process_status', batchStatus);
          }
          if (sohaUser || batchStatus) {
            window.dispatchEvent(
              new CustomEvent('auth:init', { detail: { soha_user: sohaUser, batch_process_status: batchStatus } })
            );
          }
        }
      } catch (error) {
        console.error('Init failed, but continuing app load:', error);
      }
    };
    void initialize();
  }, [authState?.isAuthenticated]);

  return null;
}

/** Shell + CSRF/bootstrap; public header always visible (matches portfolio-review-app-ui-master). */
function LayoutWrapper() {
  return (
    <AppLayout>
      {ENABLE_OKTA && <OktaAuthBootstrapper />}
      {!ENABLE_OKTA && <LocalAuthBootstrapper />}
      <Outlet />
    </AppLayout>
  );
}

function App() {
  if (ENABLE_OKTA && !oktaAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center p-6">
        <div className="max-w-xl text-center text-gray-800">
          <h1 className="text-lg font-semibold mb-2">Okta is enabled but not configured</h1>
          <p className="text-sm text-gray-600">
            Set <code className="rounded bg-gray-100 px-1.5 py-0.5">VITE_OKTA_ISSUER</code> and{' '}
            <code className="rounded bg-gray-100 px-1.5 py-0.5">VITE_OKTA_CLIENT_ID</code> (and optionally{' '}
            <code className="rounded bg-gray-100 px-1.5 py-0.5">VITE_OKTA_REDIRECT_URI</code>) in your environment, or
            set <code className="rounded bg-gray-100 px-1.5 py-0.5">VITE_ENABLE_OKTA=false</code> for local development.
          </p>
        </div>
      </div>
    );
  }

  return (
    <QueryClientProvider client={queryClient}>
      <Toaster />
      <GlobalApiActivityIndicator />
      <BrowserRouter>
        {ENABLE_OKTA ? (
          <Security
            oktaAuth={oktaAuth!}
            restoreOriginalUri={async (_oktaAuth: unknown, originalUri?: string) => {
              window.location.replace(originalUri || '/');
            }}
          >
            <Suspense fallback={<LoadingSpinner />}>
              <AppProvider>
                <Routes>
                  <Route path="/login/callback" element={<LoginCallback />} />
                  <Route path="/oauth2/login/callback" element={<LoginCallback />} />
                  <Route element={<LayoutWrapper />}>
                    <Route element={<RequireAuth />}>
                      {LAZY_PAGES.map(({ path, el }) => (
                        <Route key={path} path={path} element={el} />
                      ))}
                    </Route>
                  </Route>
                </Routes>
              </AppProvider>
            </Suspense>
          </Security>
        ) : (
          <Suspense fallback={<LoadingSpinner />}>
            <AppProvider>
              <Routes>
                <Route element={<LayoutWrapper />}>
                  <Route element={<PassThrough />}>
                    {LAZY_PAGES.map(({ path, el }) => (
                      <Route key={path} path={path} element={el} />
                    ))}
                  </Route>
                </Route>
              </Routes>
            </AppProvider>
          </Suspense>
        )}
      </BrowserRouter>
    </QueryClientProvider>
  );
}

export default App;
