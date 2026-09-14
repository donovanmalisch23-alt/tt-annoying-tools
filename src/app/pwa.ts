export function serviceWorkerSupported(): boolean {
  return typeof navigator !== "undefined" && "serviceWorker" in navigator;
}

/**
 * Registers the offline shell worker. Called in production builds, and in dev
 * only when the user explicitly turns offline mode on, so the preview never
 * serves a stale shell while iterating.
 */
export function registerServiceWorker(): void {
  if (!serviceWorkerSupported()) return;
  const secure =
    window.location.protocol === "https:" ||
    window.location.hostname === "localhost" ||
    window.location.hostname === "127.0.0.1";
  if (!secure) return;

  const register = (): void => {
    navigator.serviceWorker.register("/sw.js").catch((error: unknown) => {
      console.warn("Offline shell registration failed:", error);
    });
  };

  if (document.readyState === "complete") register();
  else window.addEventListener("load", register, { once: true });
}
