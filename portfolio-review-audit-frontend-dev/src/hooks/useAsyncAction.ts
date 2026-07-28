import { useCallback, useRef, useState } from 'react';

/**
 * Small helper for "single-flight" async actions:
 * - exposes `loading`
 * - disables duplicate clicks while request is running
 */
export function useAsyncAction<TArgs extends unknown[], TResult>(
  fn: (...args: TArgs) => Promise<TResult>,
) {
  const [loading, setLoading] = useState(false);
  const inFlight = useRef<Promise<TResult> | null>(null);

  const run = useCallback(
    async (...args: TArgs) => {
      if (inFlight.current) return inFlight.current;
      setLoading(true);
      const p = (async () => fn(...args))();
      inFlight.current = p;
      try {
        return await p;
      } finally {
        inFlight.current = null;
        setLoading(false);
      }
    },
    [fn],
  );

  return { run, loading };
}

