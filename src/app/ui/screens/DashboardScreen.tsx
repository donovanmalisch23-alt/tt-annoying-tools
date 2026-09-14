import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { logbus } from "../../core/logbus";
import { updateTimeScale, useTimeScale } from "../../core/store";
import { runManager } from "../../run/manager";
import { server } from "../../sim/instance";
import { BREAK_THREADS } from "../../sim/server";
import {
  Badge,
  Button,
  Empty,
  Field,
  Meter,
  Panel,
  Select,
  Stat,
  TextInput,
  Toggle,
} from "../components";
import { useTick } from "../hooks";

function NumberSetting({
  label,
  help,
  value,
  min,
  max,
  onChange,
}: {
  label: string;
  help?: string;
  value: number;
  min?: number;
  max?: number;
  onChange: (value: number) => void;
}): ReactElement {
  const [draft, setDraft] = useState(String(value));
  useEffect(() => {
    if (Number(draft) !== value) setDraft(String(value));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  return (
    <Field label={label} help={help}>
      <TextInput
        type="number"
        value={draft}
        min={min}
        max={max}
        onChange={(next) => {
          setDraft(next);
          const parsed = Number(next);
          if (next.trim() !== "" && Number.isFinite(parsed)) onChange(parsed);
        }}
      />
    </Field>
  );
}

export function DashboardScreen(): ReactElement {
  useTick(400);
  const settings = server.settings;
  const channels = server.listChannels();
  const users = server.listUsers();
  const speed = useTimeScale();

  useEffect(() => {
    server.syncAutoChurn();
  }, [settings.autoJoin]);

  const load = server.load;
  const running = runManager.isRunning;

  return (
    <div className="grid grid--dashboard">
      <div className="stack">
        <Panel
          title="Simulated TeamTalk server"
          subtitle="Every tool in this panel connects to this server model, not the network."
          actions={
            settings.online ? <Badge tone="ok">online</Badge> : <Badge tone="danger">offline</Badge>
          }
        >
          <div className="form-grid">
            <div className="field-row">
              <Toggle
                checked={settings.online}
                label={settings.online ? "Server online" : "Server offline"}
                onChange={(next) => {
                  settings.online = next;
                  logbus.warn(next ? "Server is online again." : "Server taken offline.");
                }}
              />
              <Toggle
                checked={settings.autoJoin}
                label="Auto join/leave churn"
                onChange={(next) => {
                  settings.autoJoin = next;
                  logbus.info(next ? "New joiners start arriving." : "Churn stopped.");
                }}
              />
              <Toggle
                checked={settings.protectionEnabled}
                label="Flood protection"
                onChange={(next) => {
                  settings.protectionEnabled = next;
                  logbus.info(`Flood protection ${next ? "enabled" : "disabled"}.`);
                }}
              />
            </div>

            <div className="form-grid form-grid--3">
              <NumberSetting
                label="Command latency (ms)"
                value={settings.latencyMs}
                min={0}
                onChange={(value) => (settings.latencyMs = Math.max(0, value))}
              />
              <NumberSetting
                label="Latency jitter (ms)"
                value={settings.jitterMs}
                min={0}
                onChange={(value) => (settings.jitterMs = Math.max(0, value))}
              />
              <NumberSetting
                label="Max users"
                value={settings.maxUsers}
                min={1}
                onChange={(value) => (settings.maxUsers = Math.max(1, Math.round(value)))}
              />
              <NumberSetting
                label="Protection burst"
                help="Commands per window before a kick."
                value={settings.protectionBurst}
                min={1}
                onChange={(value) => (settings.protectionBurst = Math.max(1, Math.round(value)))}
              />
              <NumberSetting
                label="Protection window (ms)"
                value={settings.protectionWindowMs}
                min={100}
                onChange={(value) => (settings.protectionWindowMs = Math.max(100, value))}
              />
              <Field label="Simulator speed" help="Compresses waits in the server and the tools.">
                <Select
                  value={String(speed)}
                  onChange={(next) => updateTimeScale(Number(next))}
                  options={["1", "2", "4", "8", "16"]}
                />
              </Field>
            </div>
          </div>

          <div className="row row--wrap">
            <Button
              size="sm"
              onClick={() => {
                server.spawnUser();
                logbus.info("A synthetic user joined.");
              }}
            >
              Spawn a user
            </Button>
            <Button
              size="sm"
              onClick={() => {
                server.removeOneSynthetic();
                logbus.info("A synthetic user left.");
              }}
            >
              Remove one
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => {
                server.kickAll();
                logbus.warn("Operator kicked every user on the server.");
              }}
            >
              Kick everyone
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={running}
              title={running ? "Stop the run before resetting the server" : undefined}
              onClick={() => {
                server.reset();
                logbus.sys("Server reset: channels, roster and settings are back to defaults.");
              }}
            >
              Reset server
            </Button>
          </div>
        </Panel>

        <Panel title={`Roster (${users.length}/${settings.maxUsers})`} subtitle="Live users on the simulated server.">
          {users.length === 0 ? (
            <Empty>No users online. Run a tool or spawn a synthetic user.</Empty>
          ) : (
            <div className="table-wrap">
              <table className="table">
                <thead>
                  <tr>
                    <th>ID</th>
                    <th>Nickname</th>
                    <th>Username</th>
                    <th>Channel</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {users.map((user) => (
                    <tr key={user.id}>
                      <td className="mono">{user.id}</td>
                      <td>{user.nickname || "—"}</td>
                      <td className="mono">{user.username || "—"}</td>
                      <td className="mono">{user.channelPath}</td>
                      <td className="table__actions">
                        <Button
                          size="sm"
                          variant="ghost"
                          onClick={() => {
                            const live = Array.from(server.users.values()).find(
                              (candidate) => candidate.id === user.id,
                            );
                            if (!live) return;
                            server.kick(live.sessionId, "operator kicked from the panel");
                            logbus.warn(`Kicked ${user.nickname || user.username} (id ${user.id}).`);
                          }}
                        >
                          Kick
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Panel>
      </div>

      <div className="stack">
        <Panel title="Server state">
          <div className="stats">
            <Stat label="Users online" value={`${users.length}/${settings.maxUsers}`} />
            <Stat
              label="Junk threads"
              value={server.floodThreads}
              tone={server.floodThreads > 0 ? "warn" : "default"}
            />
            <Stat
              label="Load"
              value={`${Math.round(load * 100)}%`}
              tone={load >= 0.98 ? "danger" : load > 0.5 ? "warn" : "ok"}
              hint={`saturates at ${BREAK_THREADS} threads`}
            />
            <Stat
              label="Command loss"
              value={`${Math.round(server.lossProbability * 100)}%`}
              tone={server.lossProbability > 0 ? "warn" : "ok"}
            />
          </div>
          <Meter value={load} max={1} tone={load >= 0.98 ? "danger" : "accent"} label="server load" />
          <div className="row row--wrap">
            {server.saturated ? <Badge tone="danger">saturated</Badge> : <Badge tone="ok">coping</Badge>}
            <Badge tone="muted">{channels.length} channels</Badge>
            {settings.protectionEnabled ? (
              <Badge tone="accent">protection on</Badge>
            ) : (
              <Badge tone="warn">protection off</Badge>
            )}
          </div>
        </Panel>

        <Panel title="Channels" subtitle="Path and flags as the tools discover them.">
          <ul className="list">
            {channels.map((channel) => (
              <li key={channel.id} className="list__item">
                <span className="mono">{channel.path}</span>
                <span className="list__meta">
                  #{channel.id}
                  {channel.passwordRequired && <Badge tone="warn">password</Badge>}
                  {channel.hidden && <Badge tone="muted">hidden</Badge>}
                </span>
              </li>
            ))}
          </ul>
        </Panel>

        <Panel title="How to use this panel" tone="default">
          <ol className="steps">
            <li>
              The client is set to <span className="mono">{server.settings.maxUsers} max users</span>{" "}
              and the allowlist already contains <span className="mono">127.0.0.1</span>.
            </li>
            <li>Open <strong>Tools</strong> and run something gentle, like the message sender.</li>
            <li>Watch the roster and the load meter change here, then try the load tools.</li>
            <li>Turn flood protection on to see kick resistance reconnect and resume.</li>
          </ol>
        </Panel>
      </div>
    </div>
  );
}
