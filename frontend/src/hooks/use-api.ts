"use client";

import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "../services/api/client";

type State<T> = { data: T | null; error: string | null; loading: boolean };

const DEFAULT_ERROR = "Unable to reach the RecoverX API. Check that the backend is running and retry.";

/**
 * Load data on mount and whenever `loader` changes.
 * `loader` must be stable: define it at module level or wrap it in useCallback.
 */
export function useApi<T>(loader: () => Promise<T>) {
  const [state, setState] = useState<State<T>>({ data: null, error: null, loading: true });
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let active = true;
    loader().then(
      (data) => {
        if (active) setState({ data, error: null, loading: false });
      },
      (error: unknown) => {
        if (active) setState((current) => ({ ...current, error: errorMessage(error, DEFAULT_ERROR), loading: false }));
      },
    );
    return () => {
      active = false;
    };
  }, [loader, version]);

  const reload = useCallback(() => setVersion((value) => value + 1), []);
  const setData = useCallback((data: T) => setState((current) => ({ ...current, data })), []);
  return { ...state, reload, setData };
}
