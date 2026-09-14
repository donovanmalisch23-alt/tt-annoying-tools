import { useEffect, useState } from "react";
import type { ReactElement } from "react";
import { loadOfflineEnabled, saveOfflineEnabled } from "../../core/settings";
import { updateConfig, updateWhitelist, useConfig, useWhitelist } from "../../core/store";
import { DEFAULT_CONFIG, Whitelist } from "../../core/types";
import { liveStore, useLive } from "../../live/store";
import { registerServiceWorker, serviceWorkerSupported } from "../../pwa";
import { Badge, Button, Field, Panel, TextArea, TextInput, Toggle } from "../components";
import { goToScreen } from "../nav";

interface InstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<{ outcome: string }>;
}

export function SettingsScreen(): ReactElement {
  const config = useConfig();
  const whitelist = useWhitelist();
  const live = useLive();
  const liveActive = live.mode === "live";
  const [allowlistDraft, setAllowlistDraft] = useState(whitelist.join("\n"));
  const [offline, setOffline] = useState(loadOfflineEnabled);
  const [installEvent, setInstallEvent] = useState<InstallPromptEvent | null>(null);
  const [note, setNote] = useState<string | null>(null);

  useEffect(() => {
    const handler = (event: Event): void => {
      event.preventDefault();
      setInstallEvent(event as InstallPromptEvent);
    };
    window.addEventListener("beforeinstallprompt", handler);
    return () => window.removeEventListener("beforeinstallprompt", handler);
  }, []);

  const patch = (changes: Partial<typeof config>): void => updateConfig({ ...config, ...changes });

  return (
    <div className="grid grid--settings">
      <div className="stack">
        <Panel
          title="Connection"
          subtitle="The equivalent of teamtalk.env — kept in this browser, never sent anywhere."
          actions={
            <Button size="sm" variant="ghost" onClick={() => updateConfig({ ...DEFAULT_CONFIG })}>
              Reset to defaults
            </Button>
          }
        >
          <div className="form-grid form-grid--2">
            <Field label="Server host" help="Must be on the allowlist for the gated tools.">
              <TextInput value={config.host} onChange={(value) => patch({ host: value })} />
            </Field>
            <Field label="Client nickname">
              <TextInput value={config.nickname} onChange={(value) => patch({ nickname: value })} />
            </Field>
            <Field label="TCP port">
              <TextInput
                type="number"
                value={String(config.tcpPort)}
                onChange={(value) => patch({ tcpPort: Number(value) || 0 })}
              />
            </Field>
            <Field label="UDP port">
              <TextInput
                type="number"
                value={String(config.udpPort)}
                onChange={(value) => patch({ udpPort: Number(value) || 0 })}
              />
            </Field>
            <Field label="Username" help="Blank means an anonymous login.">
              <TextInput value={config.username} onChange={(value) => patch({ username: value })} />
            </Field>
            <Field label="Password">
              <TextInput
                type="password"
                value={config.password}
                onChange={(value) => patch({ password: value })}
              />
            </Field>
            <Field label="Channel path" help="Joined on login; e.g. /Lobby">
              <TextInput
                value={config.channelPath ?? ""}
                onChange={(value) => patch({ channelPath: value })}
              />
            </Field>
            <Field label="Channel ID" help="Optional; overrides the path when set.">
              <TextInput
                type="number"
                value={config.channelId === null ? "" : String(config.channelId)}
                onChange={(value) => patch({ channelId: value.trim() === "" ? null : Number(value) })}
              />
            </Field>
            <Field label="Channel password">
              <TextInput
                type="password"
                value={config.channelPassword}
                onChange={(value) => patch({ channelPassword: value })}
              />
            </Field>
            <Field label="Command timeout (s)">
              <TextInput
                type="number"
                value={String(config.commandTimeoutSec)}
                onChange={(value) => patch({ commandTimeoutSec: Number(value) || 1 })}
              />
            </Field>
            <Field label="Reconnect delay (s)" help="How long a kicked session waits before recovering.">
              <TextInput
                type="number"
                value={String(config.reconnectDelaySec)}
                onChange={(value) => patch({ reconnectDelaySec: Number(value) || 0 })}
              />
            </Field>
            <Field label="Client name">
              <TextInput
                value={config.clientName}
                onChange={(value) => patch({ clientName: value })}
              />
            </Field>
          </div>

          <div className="field-row">
            <Toggle
              checked={config.kickResistance}
              label="Kick resistance"
              onChange={(next) => patch({ kickResistance: next })}
            />
            <Toggle
              checked={config.encrypted}
              label="Encrypted connection"
              onChange={(next) => patch({ encrypted: next })}
            />
            <Toggle
              checked={config.channelId === null}
              label="Join by path"
              onChange={(next) => patch({ channelId: next ? null : config.channelId ?? 1 })}
            />
          </div>
          <p className="prose">
            {liveActive ? (
              <>
                Live target: <span className="mono">{config.host}:{config.tcpPort}</span>. The bridge
                forwards these values to the CLI tool as its connection flags — the password travels
                in the child's environment, never on the command line.
              </>
            ) : (
              <>
                Simulated server: <span className="mono">{config.host}:{config.tcpPort}</span>. These
                values are used by the simulator's model, and travel no further than this tab.
              </>
            )}
          </p>
        </Panel>

        <Panel
          title="Allowlist"
          subtitle={
            liveActive
              ? "Live mode enforces the bridge's own file; this one only gates the simulator."
              : "Exact hostnames or IPs, one per line. # starts a comment."
          }
          actions={liveActive ? <Badge tone="warn">simulator only</Badge> : undefined}
        >
          {liveActive && (
            <div className="stack">
              <p className="alert alert--warn">
                The real gates read the bridge's file, not this one. Edit it on the Bridge &amp; admin
                tab, which requires an administrator sign-in.
              </p>
              <div className="row row--wrap">
                <span className="gate__label">Bridge file</span>
                <span className="mono">{live.whitelist?.path ?? "not connected"}</span>
                {live.whitelist ? (
                  live.whitelist.entries.length === 0 ? (
                    <Badge tone="danger">empty</Badge>
                  ) : (
                    live.whitelist.entries.map((entry) => (
                      <span key={entry} className="tag mono">
                        {entry}
                      </span>
                    ))
                  )
                ) : (
                  <Badge tone="muted">connect to the bridge to see it</Badge>
                )}
              </div>
              <div className="row">
                <Button
                  size="sm"
                  onClick={() => {
                    liveStore.setMode("live");
                    goToScreen("bridge");
                  }}
                >
                  Open the admin panel
                </Button>
                {live.whitelist && (
                  <Button size="sm" variant="ghost" onClick={() => void liveStore.reloadWhitelist()}>
                    Reload from the bridge
                  </Button>
                )}
              </div>
            </div>
          )}
          <TextArea value={allowlistDraft} onChange={setAllowlistDraft} rows={7} mono />
          <div className="row">
            <Button
              variant="primary"
              size="sm"
              onClick={() => {
                const entries = Whitelist.parse(allowlistDraft);
                updateWhitelist(entries);
                setAllowlistDraft(entries.join("\n"));
                setNote(`Allowlist saved: ${entries.length} host(s).`);
              }}
            >
              Save allowlist
            </Button>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                updateWhitelist([]);
                setAllowlistDraft("");
                setNote("Allowlist cleared — every gated tool is now refused.");
              }}
            >
              Clear
            </Button>
          </div>
          <ul className="list">
            {whitelist.map((entry) => (
              <li key={entry} className="list__item">
                <span className="mono">{entry}</span>
                <span className="list__meta">
                  {entry === Whitelist.normalize(config.host) ? (
                    <span className="badge badge--ok">current target</span>
                  ) : null}
                </span>
              </li>
            ))}
          </ul>
          <p className="prose">
            The combined suite, idle bots and the ramp test refuse any host that is not listed here.
            The local flood test has its own stronger gate: the target must be an address on this
            machine.
          </p>
        </Panel>
      </div>

      <div className="stack">
        <Panel title="Install as an app" subtitle="This panel is an installable PWA.">
          <div className="field-row">
            <Toggle
              checked={offline}
              label="Offline shell"
              onChange={(next) => {
                setOffline(next);
                saveOfflineEnabled(next);
                if (next) {
                  registerServiceWorker();
                  setNote("Service worker registered; the shell is cached for offline use.");
                } else {
                  setNote("Offline flag cleared. An installed app may keep the old worker until it reloads.");
                }
              }}
            />
            <Button
              size="sm"
              variant="primary"
              disabled={!installEvent}
              onClick={() => {
                void installEvent?.prompt();
              }}
            >
              Install
            </Button>
          </div>
          <p className="prose">
            {serviceWorkerSupported()
              ? "Add it to your home screen or desktop for a full-screen, offline-capable panel. The tools themselves keep running against the in-tab simulator, so nothing needs a network connection."
              : "This browser does not support service workers, so the panel stays a normal web page."}
          </p>
          {!installEvent && (
            <p className="prose">
              If the Install button is disabled, use your browser's own “Install app” / “Add to home
              screen” entry — it appears once the page qualifies as installable.
            </p>
          )}
          {note && <p className="alert alert--info">{note}</p>}
        </Panel>

        <Panel
          title="Simulator speed"
          subtitle="Set on the Dashboard. Every wait goes through the same clock."
        >
          <p className="prose">
            Stage durations, message intervals and reconnect delays share one time scale, so ratios —
            and therefore every verdict the load tools report — are unchanged by the speed setting.
          </p>
        </Panel>

        <Panel title="Run limits in this panel">
          <ul className="steps">
            <li>Idle bots: up to <strong>128</strong> connections.</li>
            <li>Concurrent suite mode: up to <strong>64</strong> bots.</li>
            <li>Flood threads per mode: up to <strong>64</strong>; ramp up to <strong>1024</strong>.</li>
            <li>Flood duration: capped at <strong>60 s</strong> per stage, as in the CLI.</li>
          </ul>
          <p className="prose">
            The desktop suite forks worker processes to stay under the native select() file-descriptor
            ceiling. One browser tab cannot do that, so the caps above are enforced with a clear
            refusal instead of a crash.
          </p>
        </Panel>
      </div>
    </div>
  );
}
