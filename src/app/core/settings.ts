import type { ConnectionConfig } from "./types";
import { DEFAULT_CONFIG } from "./types";

const CONFIG_KEY = "tt-web.config.v1";
const WHITELIST_KEY = "tt-web.whitelist.v1";
const OFFLINE_KEY = "tt-web.offline.v1";
const SPEED_KEY = "tt-web.speed.v1";

/** Default simulator time scale: enough compression to watch a ramp comfortably. */
export const DEFAULT_TIME_SCALE = 4;

/**
 * Equivalent of the CLI's `teamtalk.env` + `whitelist.txt`: the connection
 * defaults and the exact-host allowlist, kept in localStorage so a reload
 * does not lose what you were testing.
 */
export function loadConfig(): ConnectionConfig {
  try {
    const raw = window.localStorage.getItem(CONFIG_KEY);
    if (!raw) return { ...DEFAULT_CONFIG };
    const parsed = JSON.parse(raw) as Partial<ConnectionConfig>;
    return { ...DEFAULT_CONFIG, ...parsed };
  } catch {
    return { ...DEFAULT_CONFIG };
  }
}

export function saveConfig(config: ConnectionConfig): void {
  try {
    window.localStorage.setItem(CONFIG_KEY, JSON.stringify(config));
  } catch {
    /* storage disabled; the in-memory config still works */
  }
}

export const DEFAULT_WHITELIST = ["127.0.0.1", "localhost"];

export function loadWhitelist(): string[] {
  try {
    const raw = window.localStorage.getItem(WHITELIST_KEY);
    if (!raw) return [...DEFAULT_WHITELIST];
    const parsed = JSON.parse(raw) as unknown;
    if (!Array.isArray(parsed)) return [...DEFAULT_WHITELIST];
    const entries = parsed.filter((entry): entry is string => typeof entry === "string");
    return entries.length > 0 ? entries : [...DEFAULT_WHITELIST];
  } catch {
    return [...DEFAULT_WHITELIST];
  }
}

export function saveWhitelist(entries: string[]): void {
  try {
    window.localStorage.setItem(WHITELIST_KEY, JSON.stringify(entries));
  } catch {
    /* ignore */
  }
}

/** Whether the user turned on the offline/PWA install path explicitly. */
export function loadOfflineEnabled(): boolean {
  try {
    return window.localStorage.getItem(OFFLINE_KEY) === "true";
  } catch {
    return false;
  }
}

export function saveOfflineEnabled(enabled: boolean): void {
  try {
    window.localStorage.setItem(OFFLINE_KEY, enabled ? "true" : "false");
  } catch {
    /* ignore */
  }
}

/** The simulator's time scale, so a reload keeps the speed you chose. */
export function loadTimeScale(): number {
  try {
    const raw = window.localStorage.getItem(SPEED_KEY);
    const parsed = Number(raw);
    return Number.isFinite(parsed) && parsed > 0 ? parsed : DEFAULT_TIME_SCALE;
  } catch {
    return DEFAULT_TIME_SCALE;
  }
}

export function saveTimeScale(scale: number): void {
  try {
    window.localStorage.setItem(SPEED_KEY, String(scale));
  } catch {
    /* ignore */
  }
}
