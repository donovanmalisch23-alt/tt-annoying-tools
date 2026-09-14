import { useSyncExternalStore } from "react";

export type ScreenId = "dashboard" | "tools" | "console" | "settings" | "bridge" | "about";

type Listener = () => void;

/**
 * Which tab is showing. A store rather than component state because screens
 * link to each other — the allowlist panel sends you to the admin panel, and
 * the admin panel sends you back to the tools.
 */
class NavStore {
  private value: ScreenId = "dashboard";
  private listeners = new Set<Listener>();

  get = (): ScreenId => this.value;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  go = (next: ScreenId): void => {
    if (next === this.value) return;
    this.value = next;
    this.listeners.forEach((listener) => listener());
  };
}

export const navStore = new NavStore();

export function useScreen(): ScreenId {
  return useSyncExternalStore(navStore.subscribe, navStore.get, navStore.get);
}

export function goToScreen(id: ScreenId): void {
  navStore.go(id);
}
