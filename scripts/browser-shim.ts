/**
 * Minimal browser shim so the panel's shared modules (which read localStorage
 * and use window timers) can be exercised headlessly by `scripts/smoke.ts`.
 * Not part of the app bundle.
 */
const entries = new Map<string, string>();

const storage = {
  getItem: (key: string): string | null => entries.get(key) ?? null,
  setItem: (key: string, value: string): void => {
    entries.set(key, String(value));
  },
  removeItem: (key: string): void => {
    entries.delete(key);
  },
  clear: (): void => entries.clear(),
  key: (index: number): string | null => Array.from(entries.keys())[index] ?? null,
  get length(): number {
    return entries.size;
  },
};

const target = globalThis as unknown as Record<string, unknown>;

target.window = {
  localStorage: storage,
  location: { protocol: "http:", hostname: "localhost" },
  setTimeout,
  clearTimeout,
  setInterval,
  clearInterval,
  addEventListener: () => undefined,
  removeEventListener: () => undefined,
  matchMedia: () => ({
    matches: false,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }),
};

target.localStorage = storage;
