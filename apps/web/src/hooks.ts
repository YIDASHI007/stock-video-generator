import {useCallback, useEffect, useState} from "react";

export const usePolling = <T,>(
  loader: () => Promise<T>,
  intervalMilliseconds = 2000,
): {data: T | null; error: string | null; loading: boolean; refresh: () => void} => {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);
  const refresh = useCallback(() => setVersion((value) => value + 1), []);

  useEffect(() => {
    let active = true;
    const run = async () => {
      try {
        const next = await loader();
        if (active) {
          setData(next);
          setError(null);
        }
      } catch (reason) {
        if (active) {
          setError(reason instanceof Error ? reason.message : String(reason));
        }
      } finally {
        if (active) setLoading(false);
      }
    };
    void run();
    const timer = window.setInterval(run, intervalMilliseconds);
    return () => {
      active = false;
      window.clearInterval(timer);
    };
  }, [loader, intervalMilliseconds, version]);

  return {data, error, loading, refresh};
};

