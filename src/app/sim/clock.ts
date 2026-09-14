/**
 * One global time scale for the whole simulator. 1 = real time; higher values
 * compress sleeps so a 7-stage ramp is watchable instead of a two-minute wait.
 * Both the simulated server and the tools' own sleeps go through here, so
 * relative timing (and therefore every classification and verdict) is kept.
 */
let timeScale = 1;

/**
 * Simulated time. Wall-clock waits are compressed by the time scale, so a
 * window measured with `Date.now()` would shrink (or stretch) with the speed
 * setting — the server's flood protection and the bot cooldowns must not.
 * Everything that judges a *rate* uses this clock instead, which keeps those
 * verdicts identical at 1× and at 30×.
 */
let virtualOffset = 0;

export function setTimeScale(scale: number): void {
  timeScale = scale > 0 ? scale : 1;
}

export function getTimeScale(): number {
  return timeScale;
}

export function scaled(ms: number): number {
  return Math.max(0, ms / timeScale);
}

/** Milliseconds on the simulated clock. */
export function simNow(): number {
  return Date.now() + virtualOffset;
}

/**
 * Accounts for `realElapsedMs` of wall time that has passed: on the simulated
 * clock that period is worth `realElapsedMs * timeScale`. Call it after any
 * wait (full, chunked or interrupted) so `simNow()` tracks it.
 */
export function advanceReal(realElapsedMs: number): void {
  if (timeScale === 1 || realElapsedMs <= 0) return;
  virtualOffset += realElapsedMs * (timeScale - 1);
}

export async function sleep(ms: number): Promise<void> {
  const wait = scaled(ms);
  await sleepRaw(wait);
  advanceReal(wait);
}

/** Unscaled wait, used by stop-aware sleep loops that already scaled their budget. */
export function sleepRaw(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
