import { useState } from "react";
import type { ReactElement } from "react";
import type { FieldSpec, Values } from "../../core/registry";
import { defaultValues, TOOL_SPECS } from "../../core/registry";
import { configStore, useConfig, useWhitelist } from "../../core/store";
import { Whitelist } from "../../core/types";
import { liveStore, useLive } from "../../live/store";
import { runManager } from "../../run/manager";
import { goToScreen } from "../nav";
import {
  Badge,
  Button,
  Field,
  Panel,
  Select,
  Stat,
  TextArea,
  TextInput,
  Toggle,
} from "../components";
import { useRunSnapshot } from "../hooks";

function control(
  field: FieldSpec,
  value: string,
  onChange: (next: string) => void,
): ReactElement {
  switch (field.kind) {
    case "multiline":
      return <TextArea value={value} onChange={onChange} rows={3} />;
    case "toggle":
      return (
        <Toggle
          checked={value === "true"}
          label={value === "true" ? "Enabled" : "Disabled"}
          onChange={(next) => onChange(next ? "true" : "false")}
        />
      );
    case "choice":
      return <Select value={value} onChange={onChange} options={field.choices ?? []} />;
    case "int":
    case "number":
      return <TextInput type="number" value={value} onChange={onChange} />;
    case "secret":
      return <TextInput type="password" value={value} onChange={onChange} />;
    default:
      return <TextInput value={value} onChange={onChange} placeholder={field.default} />;
  }
}

export function ToolsScreen(): ReactElement {
  const run = useRunSnapshot();
  const config = useConfig();
  const whitelist = useWhitelist();
  const live = useLive();

  const [selectedId, setSelectedId] = useState(TOOL_SPECS[0]?.id ?? "");
  const [values, setValues] = useState<Record<string, Values>>(() => {
    const initial: Record<string, Values> = {};
    for (const spec of TOOL_SPECS) initial[spec.id] = defaultValues(spec.id);
    return initial;
  });
  const [confirmed, setConfirmed] = useState<Record<string, boolean>>({});
  const [error, setError] = useState<string | null>(null);

  const spec = TOOL_SPECS.find((candidate) => candidate.id === selectedId) ?? TOOL_SPECS[0];
  const soft = TOOL_SPECS.filter((candidate) => candidate.soft);
  const heavy = TOOL_SPECS.filter((candidate) => !candidate.soft);

  const liveActive = live.mode === "live";
  const bridgeOnline = live.status === "online";
  /** In live mode the bridge's file is the one that counts. */
  const allowlist = liveActive ? (live.whitelist?.entries ?? []) : whitelist;
  const liveRun = liveActive ? live.run : null;
  const runningThisTool = liveActive
    ? liveRun?.state === "running" && liveRun.tool === spec.id
    : run.state === "running" && run.toolId === spec.id;
  const anyRunActive = liveActive ? liveRun?.state === "running" : run.state === "running";
  const allowed = Whitelist.isAllowed(config.host, allowlist);
  const blocked = spec.requiresWhitelist && !allowed;
  const blockedByBridge = liveActive && !bridgeOnline;
  /** Fields the bridge does not forward to the CLI (the tool's own spacing wins). */
  const ignoredKeys = liveActive
    ? (live.tools.find((tool) => tool.id === spec.id)?.ignored ?? [])
    : [];

  const setField = (key: string, next: string): void => {
    setValues((current) => ({
      ...current,
      [spec.id]: { ...current[spec.id], [key]: next },
    }));
  };

  const start = (): void => {
    setError(null);
    const confirmedNow = Boolean(confirmed[spec.id]);
    if (liveActive) {
      void liveStore
        .startRun(spec.id, values[spec.id] ?? {}, configStore.get(), confirmedNow)
        .catch((failure: unknown) => {
          setError(failure instanceof Error ? failure.message : String(failure));
        });
      return;
    }
    try {
      runManager.start({
        toolId: spec.id,
        values: values[spec.id] ?? {},
        config: configStore.get(),
        whitelist,
        confirmed: confirmedNow,
      });
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    }
  };

  const stop = (): void => {
    if (liveActive) void liveStore.stopRun();
    else runManager.stop();
  };

  const statusOf = (candidate: (typeof TOOL_SPECS)[number]): string | null => {
    if (liveActive) return liveRun?.tool === candidate.id ? liveRun.state : null;
    return run.toolId === candidate.id && run.state !== "idle" ? run.state : null;
  };

  const card = (candidate: (typeof TOOL_SPECS)[number]): ReactElement => {
    const status = statusOf(candidate);
    return (
      <button
        key={candidate.id}
        type="button"
        className={`tool-card${candidate.id === spec.id ? " tool-card--active" : ""}`}
        onClick={() => setSelectedId(candidate.id)}
      >
        <span className="tool-card__title">{candidate.title}</span>
        <span className="tool-card__tagline">{candidate.tagline}</span>
        <span className="tool-card__meta">
          {status && (
            <Badge
              tone={
                status === "running"
                  ? "accent"
                  : status === "failed"
                    ? "danger"
                    : status === "cancelled" || status === "stopped"
                      ? "warn"
                      : "ok"
              }
            >
              {status}
            </Badge>
          )}
          {candidate.requiresConfirm && <Badge tone="muted">confirm</Badge>}
        </span>
      </button>
    );
  };

  return (
    <div className="grid grid--tools">
      <div className="stack">
        <Panel
          title="Run target"
          subtitle={
            liveActive
              ? "Live: the bridge starts the repository's own CLI tool as a child process."
              : "Simulated: runs act on the in-tab server model."
          }
        >
          <div className="row row--wrap">
            {liveActive ? (
              <>
                <Badge tone={bridgeOnline ? "ok" : "danger"}>
                  {bridgeOnline ? "bridge online" : `bridge ${live.status}`}
                </Badge>
                <span className="mono">{live.base || "this origin"}</span>
                {live.whitelist && (
                  <span className="prose mono">{live.whitelist.path}</span>
                )}
              </>
            ) : (
              <Badge tone="muted">simulated</Badge>
            )}
            <Button size="sm" variant="ghost" onClick={() => liveStore.setMode(liveActive ? "sim" : "live")}>
              Switch to {liveActive ? "simulated" : "live server"}
            </Button>
          </div>
        </Panel>

        <Panel title="Gentle tools" subtitle="Safe on any server you are allowed to test.">
          <div className="tool-list">{soft.map(card)}</div>
        </Panel>
        <Panel
          title="Load tools"
          subtitle="Focused on the server's capacity. Read the gates before running."
          tone="danger"
        >
          <div className="tool-list">{heavy.map(card)}</div>
        </Panel>
      </div>

      <div className="stack">
        <Panel
          title={spec.title}
          subtitle={spec.tagline}
          actions={
            anyRunActive ? (
              runningThisTool ? (
                <Button variant="danger" size="sm" onClick={stop}>
                  Stop
                </Button>
              ) : (
                <Badge tone="warn">another run is active</Badge>
              )
            ) : null
          }
        >
          <p className="prose">{spec.description}</p>

          <div className="form-grid form-grid--2">
            {spec.fields.map((field) => (
              <Field
                key={field.key}
                label={field.label}
                help={
                  ignoredKeys.includes(field.key)
                    ? `${field.help ?? ""} Not used in live mode: the bridge lets the tool space its own launches.`.trim()
                    : field.help
                }
              >
                {control(field, values[spec.id]?.[field.key] ?? field.default, (next) =>
                  setField(field.key, next),
                )}
              </Field>
            ))}
          </div>

          <div className="gates">
            <div className="gate">
              <span className="gate__label">Target</span>
              <span className="mono">
                {config.host}:{config.tcpPort}
              </span>
              {spec.requiresWhitelist ? (
                allowed ? (
                  <Badge tone="ok">allowlisted</Badge>
                ) : (
                  <Badge tone="danger">not allowlisted</Badge>
                )
              ) : (
                <Badge tone="muted">no allowlist gate</Badge>
              )}
            </div>

            {spec.requiresConfirm && (
              <div className="gate">
                <Toggle
                  checked={Boolean(confirmed[spec.id])}
                  label="I confirm this run is authorised"
                  onChange={(next) => setConfirmed((current) => ({ ...current, [spec.id]: next }))}
                />
              </div>
            )}
          </div>

          {blockedByBridge && (
            <div className="alert alert--danger">
              <p>
                Live mode needs the bridge. Start it with{" "}
                <span className="mono">./run_webby.sh start</span>, then connect from the Bridge &amp;
                admin tab.
              </p>
              <Button size="sm" onClick={() => goToScreen("bridge")}>
                Open Bridge &amp; admin
              </Button>
            </div>
          )}
          {blocked && (
            <p className="alert alert--warn">
              Add <span className="mono">{config.host}</span>{" "}
              {liveActive ? (
                <>
                  to the allowlist file on the Bridge &amp; admin tab (admin sign-in required).
                </>
              ) : (
                <>on the Allowlist screen before running this tool.</>
              )}
            </p>
          )}
          {error && <p className="alert alert--danger">{error}</p>}
          {live.note && liveActive && <p className="alert alert--ok">{live.note}</p>}

          <div className="row">
            <Button
              variant="primary"
              onClick={start}
              disabled={
                anyRunActive || blocked || blockedByBridge || (spec.requiresConfirm && !confirmed[spec.id])
              }
            >
              {statusOf(spec) && statusOf(spec) !== "running" ? "Run again" : "Run"}
            </Button>
            <Button
              variant="ghost"
              onClick={() => setValues((current) => ({ ...current, [spec.id]: defaultValues(spec.id) }))}
            >
              Reset fields
            </Button>
          </div>
        </Panel>

        <Panel
          title="Last run"
          subtitle={liveActive ? `Bridge run: ${liveRun?.state ?? "none"}` : `State: ${run.state}`}
        >
          {liveActive ? (
            liveRun && liveRun.tool === spec.id ? (
              <div className="stack">
                <div className="stats">
                  <Stat label="Script" value={liveRun.script} />
                  <Stat label="Host" value={liveRun.host || "—"} />
                  <Stat label="State" value={liveRun.state} />
                  <Stat
                    label="Exit"
                    value={liveRun.exit_code === null ? "—" : String(liveRun.exit_code)}
                  />
                </div>
                <pre className="code">{liveRun.argv.join(" ")}</pre>
                {liveRun.error && <p className="alert alert--danger">{liveRun.error}</p>}
                <p className="prose">The Console tab has the full output.</p>
              </div>
            ) : (
              <p className="prose">
                {liveRun
                  ? `The last bridge run was ${liveRun.label}. Start this tool to see it here.`
                  : "No live run yet in this bridge session."}
              </p>
            )
          ) : run.toolId === spec.id && run.state !== "idle" ? (
            <>
              {run.results.length > 0 ? (
                <div className="stats">
                  {run.results.map((result) => (
                    <Stat key={result.label} label={result.label} value={result.value} />
                  ))}
                </div>
              ) : (
                <p className="prose">
                  {run.state === "running"
                    ? "Running — the Console tab shows the live output."
                    : "No results were recorded for the last run."}
                </p>
              )}
              {run.error && <p className="alert alert--danger">{run.error}</p>}
            </>
          ) : (
            <p className="prose">This tool has not run in this mode yet.</p>
          )}
        </Panel>
      </div>
    </div>
  );
}
