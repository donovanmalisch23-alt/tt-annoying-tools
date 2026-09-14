import { useSyncExternalStore } from "react";
import { setTimeScale } from "../sim/clock";
import type { ConnectionConfig } from "./types";
import {
  loadConfig,
  loadTimeScale,
  loadWhitelist,
  saveConfig,
  saveTimeScale,
  saveWhitelist,
} from "./settings";

type Listener = () => void;

/** Tiny observable store: enough for the two pieces of shared app config. */
class Store<T> {
  private value: T;
  private listeners = new Set<Listener>();

  constructor(initial: T) {
    this.value = initial;
  }

  get = (): T => this.value;

  set = (next: T): void => {
    this.value = next;
    this.listeners.forEach((listener) => listener());
  };

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
}

export const configStore = new Store<ConnectionConfig>(loadConfig());
export const whitelistStore = new Store<string[]>(loadWhitelist());
export const timeScaleStore = new Store<number>(loadTimeScale());

// Apply the remembered simulator speed to the shared clock at start-up.
setTimeScale(timeScaleStore.get());

export function updateConfig(next: ConnectionConfig): void {
  configStore.set(next);
  saveConfig(next);
}

export function updateWhitelist(next: string[]): void {
  whitelistStore.set(next);
  saveWhitelist(next);
}

export function useConfig(): ConnectionConfig {
  return useSyncExternalStore(configStore.subscribe, configStore.get, configStore.get);
}

export function useWhitelist(): string[] {
  return useSyncExternalStore(whitelistStore.subscribe, whitelistStore.get, whitelistStore.get);
}

/** Simulator time scale, honoured by the server model and by every tool wait. */
export function updateTimeScale(scale: number): void {
  const safe = scale > 0 ? scale : 1;
  setTimeScale(safe);
  timeScaleStore.set(safe);
  saveTimeScale(safe);
}

export function useTimeScale(): number {
  return useSyncExternalStore(timeScaleStore.subscribe, timeScaleStore.get, timeScaleStore.get);
}
