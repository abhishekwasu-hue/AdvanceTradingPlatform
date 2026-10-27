import { useEffect, useState } from "react";
import { api } from "../api/client";
import { rememberStrategyNames } from "./strategyName";

/**
 * User-friendly pass item 6: the v2 shell reads the Library (`GET /api/strategies`: the built-in strategies and the
 * user's own saved ones, each with its display name) once per signed-in user and remembers the names, so every screen
 * that prints a strategy or a bot names it properly (src/ui/strategyName.ts). A failed read changes nothing: the
 * built-in names and the words rule still apply. The returned count re-renders the shell once the names arrive.
 */
export function useStrategyNames(userId: number | null): number {
  const [loaded, setLoaded] = useState(0);
  useEffect(() => {
    if (userId === null) return;
    let current = true;
    api.listStrategies().then((list) => {
      if (!current || !Array.isArray(list)) return;
      rememberStrategyNames(list.map((s) => [s.id, s.name] as const));
      setLoaded(list.length);
    }, () => { /* the built-in names stay */ });
    return () => { current = false; };
  }, [userId]);
  return loaded;
}
