/** Tracks in-flight `apiClient` (axios) requests for a global "please wait" UI. */

let count = 0;
const listeners = new Set<() => void>();

function emit() {
  for (const l of listeners) l();
}

export function getInFlightRequestCount(): number {
  return count;
}

export function subscribeInFlightRequestCount(onChange: () => void): () => void {
  listeners.add(onChange);
  return () => listeners.delete(onChange);
}

export function beginApiRequest(): void {
  count += 1;
  emit();
}

export function endApiRequest(): void {
  count = Math.max(0, count - 1);
  emit();
}
