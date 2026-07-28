import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useLocation } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import {
  getInFlightRequestCount,
  subscribeInFlightRequestCount,
} from '@/api/globalApiLoadingStore';

// How long after a route change we still treat a *starting* request as that page's
// initial load. Generous on purpose — it only ever decides whether we may SHOW, and we
// only show while a request is genuinely in flight. So a long window never causes a
// flicker on pages that don't fetch (no request → no indicator); it just makes sure we
// don't miss a fetch that starts a little late (lazy-chunk load + mount before the call).
const NAV_WINDOW_MS = 2500;
// Bridge brief gaps between back-to-back requests so the indicator doesn't flicker.
const HIDE_DEBOUNCE_MS = 150;
// Keep it on screen long enough to be perceived even when the response is near-instant
// (e.g. localhost), so it never flashes away before the user notices it.
const MIN_VISIBLE_MS = 500;
// Safety net: never let the indicator stay up indefinitely if a request hangs.
const MAX_VISIBLE_MS = 15000;

/**
 * Shows a centered "Loading…" spinner while a freshly-opened page is fetching its first
 * data, so pages give feedback instead of sitting blank. It deliberately does NOT dim,
 * blur, or cover the page — there is no backdrop; the spinner sits in the centre of the
 * content area, is click-through (pointer-events-none), and the content behind it stays
 * fully visible and interactive.
 *
 * Driven by the universal in-flight request counter (every apiClient call feeds it) and
 * gated to recent navigation so ordinary background refetches don't trigger it. Mounted
 * once inside AppLayout's <main>, so it covers every route without per-page wiring.
 */
export default function RouteTransitionLoader() {
  const { pathname } = useLocation();
  const inFlight = useSyncExternalStore(
    subscribeInFlightRequestCount,
    getInFlightRequestCount,
    getInFlightRequestCount,
  );
  const [visible, setVisible] = useState(false);
  const lastNavRef = useRef(0);
  const shownAtRef = useRef(0);
  const hideTimer = useRef<number | null>(null);
  const maxTimer = useRef<number | null>(null);

  // Stamp every route change (and the initial mount) so we know a navigation just happened.
  useEffect(() => {
    lastNavRef.current = Date.now();
  }, [pathname]);

  useEffect(() => {
    const recentlyNavigated = Date.now() - lastNavRef.current < NAV_WINDOW_MS;
    if (inFlight > 0 && (recentlyNavigated || visible)) {
      if (hideTimer.current) {
        window.clearTimeout(hideTimer.current);
        hideTimer.current = null;
      }
      if (!visible) {
        setVisible(true);
        shownAtRef.current = Date.now();
        if (maxTimer.current) window.clearTimeout(maxTimer.current);
        maxTimer.current = window.setTimeout(() => setVisible(false), MAX_VISIBLE_MS);
      }
    } else if (inFlight === 0 && visible) {
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
      // Honour a minimum visible time so a near-instant response still registers.
      const elapsed = Date.now() - shownAtRef.current;
      const wait = Math.max(HIDE_DEBOUNCE_MS, MIN_VISIBLE_MS - elapsed);
      hideTimer.current = window.setTimeout(() => {
        setVisible(false);
        if (maxTimer.current) {
          window.clearTimeout(maxTimer.current);
          maxTimer.current = null;
        }
      }, wait);
    }
  }, [inFlight, pathname, visible]);

  // Clear any pending timers on unmount.
  useEffect(
    () => () => {
      if (hideTimer.current) window.clearTimeout(hideTimer.current);
      if (maxTimer.current) window.clearTimeout(maxTimer.current);
    },
    [],
  );

  if (!visible) return null;

  return (
    <div
      className="pointer-events-none absolute inset-0 z-50 flex items-center justify-center"
      role="status"
      aria-live="polite"
      aria-busy="true"
    >
      <div className="flex items-center">
        <Loader2 className="h-10 w-10 animate-spin text-blue-500" aria-hidden />
        <span className="ml-3 text-lg font-medium text-gray-600">Loading…</span>
      </div>
    </div>
  );
}
