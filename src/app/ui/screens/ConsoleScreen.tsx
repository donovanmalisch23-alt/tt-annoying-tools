import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import type { LogLevel } from "../../core/logbus";
import { logbus } from "../../core/logbus";
import { Badge, Button, Panel } from "../components";
import { useLogLines } from "../hooks";

const FILTERS: Array<LogLevel | "all"> = ["all", "info", "ok", "warn", "error", "sys"];

function clockTime(time: number): string {
  const date = new Date(time);
  return [date.getHours(), date.getMinutes(), date.getSeconds()]
    .map((part) => String(part).padStart(2, "0"))
    .join(":");
}

export function ConsoleScreen(): ReactElement {
  const lines = useLogLines();
  const [level, setLevel] = useState<LogLevel | "all">("all");
  const [follow, setFollow] = useState(true);
  const [note, setNote] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    if (follow) endRef.current?.scrollIntoView({ block: "end" });
  }, [lines, follow, level]);

  const shown = level === "all" ? lines : lines.filter((line) => line.level === level);
  const counts = FILTERS.reduce<Record<string, number>>((accumulator, key) => {
    accumulator[key] = key === "all" ? lines.length : lines.filter((line) => line.level === key).length;
    return accumulator;
  }, {});

  const download = (): void => {
    const blob = new Blob([logbus.dump()], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `tt-web-console-${new Date().toISOString().replace(/[:.]/g, "-")}.log`;
    anchor.click();
    URL.revokeObjectURL(url);
    setNote("Log downloaded.");
  };

  const copy = (): void => {
    navigator.clipboard
      .writeText(logbus.dump())
      .then(() => setNote("Log copied to the clipboard."))
      .catch(() => setNote("The clipboard is blocked in this context; use Download instead."));
  };

  return (
    <Panel
      title="Console"
      subtitle={`${lines.length} line(s) buffered in this tab (newest last)`}
      actions={
        <div className="row">
          <Button size="sm" onClick={() => setFollow((value) => !value)}>
            {follow ? "Follow: on" : "Follow: off"}
          </Button>
          <Button size="sm" variant="ghost" onClick={copy}>
            Copy
          </Button>
          <Button size="sm" variant="ghost" onClick={download}>
            Download
          </Button>
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              logbus.clear();
              setNote("Console cleared.");
            }}
          >
            Clear
          </Button>
        </div>
      }
    >
      <div className="row row--wrap console__filters">
        {FILTERS.map((key) => (
          <button
            key={key}
            type="button"
            className={`chip${key === level ? " chip--active" : ""}`}
            onClick={() => setLevel(key)}
          >
            {key} <span className="chip__count">{counts[key]}</span>
          </button>
        ))}
        {note && <Badge tone="accent">{note}</Badge>}
      </div>

      <div className="console">
        {shown.length === 0 ? (
          <p className="empty">Nothing logged at this level yet.</p>
        ) : (
          shown.map((line) => (
            <div key={line.id} className={`logline logline--${line.level}`}>
              <span className="logline__time">{clockTime(line.time)}</span>
              <span className="logline__level">{line.level.toUpperCase()}</span>
              <span className="logline__text">{line.text}</span>
            </div>
          ))
        )}
        <div ref={endRef} />
      </div>
    </Panel>
  );
}
