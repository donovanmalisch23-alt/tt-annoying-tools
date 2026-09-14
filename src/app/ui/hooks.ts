import { useEffect, useState, useSyncExternalStore } from "react";
import type { LogLine } from "../core/logbus";
import { logbus } from "../core/logbus";
import type { RunSnapshot } from "../run/manager";
import { runManager } from "../run/manager";

/** Live log lines, re-rendered whenever the bus publishes. */
export function useLogLines(): LogLine[] {
  // The third argument keeps the hook usable in a headless render check.
  return useSyncExternalStore(logbus.subscribe, logbus.getLines, logbus.getLines);
}

/** The current run's snapshot. */
export function useRunSnapshot(): RunSnapshot {
  return useSyncExternalStore(runManager.subscribe, runManager.getSnapshot, runManager.getSnapshot);
}

/**
 * A re-render tick. The simulated server is mutable shared state, so the
 * panels that display it poll on a short interval.
 */
export function useTick(ms: number): number {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((value) => value + 1), ms);
    return () => window.clearInterval(id);
  }, [ms]);
  return tick;
}

/** True while the viewport is narrower than `query`. */
export function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);
  useEffect(() => {
    const list = window.matchMedia(query);
    const onChange = () => setMatches(list.matches);
    list.addEventListener("change", onChange);
    return () => list.removeEventListener("change", onChange);
  }, [query]);
  return matches;
}
