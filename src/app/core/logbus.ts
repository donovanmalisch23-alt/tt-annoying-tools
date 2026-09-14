export type LogLevel = "info" | "ok" | "warn" | "error" | "sys";

export interface LogLine {
  id: number;
  time: number;
  level: LogLevel;
  text: string;
}

type Listener = () => void;

const MAX_LINES = 1500;

/**
 * Single log sink for every tool plus the UI. Bounded so a long soak cannot
 * grow the tab without limit, and observable so the console updates live.
 */
class LogBus {
  private lines: LogLine[] = [];
  private listeners = new Set<Listener>();
  private nextId = 1;

  subscribe = (listener: Listener): (() => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };

  getLines = (): LogLine[] => this.lines;

  private emit(level: LogLevel, text: string): void {
    const line: LogLine = { id: this.nextId++, time: Date.now(), level, text };
    const next = [...this.lines, line];
    this.lines = next.length > MAX_LINES ? next.slice(next.length - MAX_LINES) : next;
    this.listeners.forEach((listener) => listener());
  }

  info = (text: string): void => this.emit("info", text);
  ok = (text: string): void => this.emit("ok", text);
  warn = (text: string): void => this.emit("warn", text);
  error = (text: string): void => this.emit("error", text);
  sys = (text: string): void => this.emit("sys", text);

  clear = (): void => {
    this.lines = [];
    this.listeners.forEach((listener) => listener());
  };

  dump = (): string =>
    this.lines
      .map((line) => `${new Date(line.time).toISOString()}  [${line.level}] ${line.text}`)
      .join("\n");
}

export const logbus = new LogBus();
