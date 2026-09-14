import { useEffect } from "react";
import type { ReactElement } from "react";
import { useConfig, useWhitelist } from "./core/store";
import { liveStore, useLive } from "./live/store";
import { runManager } from "./run/manager";
import { server } from "./sim/instance";
import { Badge, Button } from "./ui/components";
import { useLogLines, useRunSnapshot, useTick } from "./ui/hooks";
import type { ScreenId } from "./ui/nav";
import { goToScreen, useScreen } from "./ui/nav";
import { AboutScreen } from "./ui/screens/AboutScreen";
import { BridgeScreen } from "./ui/screens/BridgeScreen";
import { ConsoleScreen } from "./ui/screens/ConsoleScreen";
import { DashboardScreen } from "./ui/screens/DashboardScreen";
import { SettingsScreen } from "./ui/screens/SettingsScreen";
import { ToolsScreen } from "./ui/screens/ToolsScreen";

const TABS: Array<{ id: ScreenId; label: string; hint: string }> = [
  { id: "dashboard", label: "Dashboard", hint: "Simulated server and live state" },
  { id: "tools", label: "Tools", hint: "Every ported tool" },
  { id: "console", label: "Console", hint: "Live run output" },
  { id: "settings", label: "Server & allowlist", hint: "Connection config" },
  { id: "bridge", label: "Bridge & admin", hint: "Live mode, sign-in, allowlist file" },
  { id: "about", label: "About", hint: "What this is and what it is not" },
];

function elapsed(from: number, to: number): string {
  const total = Math.max(0, Math.round((to - from) / 1000));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function ModeSwitch(): ReactElement {
  const live = useLive();
  const liveActive = live.mode === "live";
  return (
    <div className="modeswitch" role="group" aria-label="Run target">
      <button
        type="button"
        className={`modeswitch__option${liveActive ? "" : " modeswitch__option--active"}`}
        onClick={() => liveStore.setMode("sim")}
        title="Run the tools against the in-tab server model"
      >
        Simulated
      </button>
      <button
        type="button"
        className={`modeswitch__option${liveActive ? " modeswitch__option--active modeswitch__option--live" : ""}`}
        onClick={() => liveStore.setMode("live")}
        title="Run the real CLI tools through the Webby bridge"
      >
        Live server
      </button>
    </div>
  );
}

export function App(): ReactElement {
  const screen = useScreen();
  const run = useRunSnapshot();
  const live = useLive();
  const lines = useLogLines();
  const config = useConfig();
  const whitelist = useWhitelist();
  useTick(500);

  useEffect(() => {
    liveStore.init();
  }, []);

  const errors = lines.filter((line) => line.level === "error").length;
  const liveActive = live.mode === "live";
  const running = liveActive ? live.run?.state === "running" : run.state === "running";
  const activeLabel = liveActive ? (live.run?.label ?? "bridge run") : run.toolTitle;
  const startedAt = liveActive
    ? (live.run?.started_at ?? 0) * 1000 || Date.now()
    : (run.startedAt ?? Date.now());

  return (
    <div className="app">
      <header className="app__header">
        <div className="brand">
          <span className="brand__mark" aria-hidden="true">
            TT
          </span>
          <span className="brand__text">
            <span className="brand__title">Annoying Tools</span>
            <span className="brand__sub">
              TeamTalk churn &amp; load panel — {liveActive ? "live bridge" : "simulated"}
            </span>
          </span>
        </div>

        <div className="statusbar">
          <span className="statusbar__item">
            <span className="statusbar__label">Target</span>
            <span className="mono">
              {config.host}:{config.tcpPort}
            </span>
          </span>
          {liveActive ? (
            <>
              <span className="statusbar__item">
                <span className="statusbar__label">Bridge</span>
                <span className="mono">
                  {live.status === "online"
                    ? (live.base || "this origin")
                    : live.status}
                </span>
              </span>
              <span className="statusbar__item">
                <span className="statusbar__label">Allowlist</span>
                <span className="mono">
                  {live.whitelist ? `${live.whitelist.count} host(s)` : "unknown"}
                </span>
              </span>
              <span className="statusbar__item">
                <span className="statusbar__label">Admin</span>
                <span className="mono">{live.signedIn ? (live.username ?? "yes") : "no"}</span>
              </span>
            </>
          ) : (
            <>
              <span className="statusbar__item">
                <span className="statusbar__label">Users</span>
                <span className="mono">
                  {server.users.size}/{server.settings.maxUsers}
                </span>
              </span>
              <span className="statusbar__item">
                <span className="statusbar__label">Load</span>
                <span className="mono">{Math.round(server.load * 100)}%</span>
              </span>
              <span className="statusbar__item">
                <span className="statusbar__label">Allowlist</span>
                <span className="mono">{whitelist.length} host(s)</span>
              </span>
            </>
          )}
        </div>

        <div className="app__runactions">
          <ModeSwitch />
          {running ? (
            <>
              <Badge tone="accent">{activeLabel}</Badge>
              <span className="mono">{elapsed(startedAt, Date.now())}</span>
              <Button
                size="sm"
                variant="danger"
                onClick={() => {
                  if (liveActive) void liveStore.stopRun();
                  else runManager.stop();
                }}
              >
                Stop
              </Button>
            </>
          ) : liveActive ? (
            <Badge tone={live.status === "online" ? "ok" : "danger"}>
              {live.status === "online" ? "bridge ready" : "bridge offline"}
            </Badge>
          ) : (
            <Badge tone={server.settings.online ? "ok" : "danger"}>
              {server.settings.online ? "server online" : "server offline"}
            </Badge>
          )}
        </div>
      </header>

      <nav className="app__nav" aria-label="Sections">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            type="button"
            className={`tab${screen === tab.id ? " tab--active" : ""}`}
            title={tab.hint}
            onClick={() => goToScreen(tab.id)}
          >
            {tab.label}
            {tab.id === "console" && errors > 0 && <span className="tab__dot">{errors}</span>}
            {tab.id === "tools" && running && <span className="tab__dot tab__dot--live" />}
            {tab.id === "bridge" && liveActive && live.status === "offline" && (
              <span className="tab__dot">!</span>
            )}
          </button>
        ))}
      </nav>

      <main className="app__main">
        {screen === "dashboard" && <DashboardScreen />}
        {screen === "tools" && <ToolsScreen />}
        {screen === "console" && <ConsoleScreen />}
        {screen === "settings" && <SettingsScreen />}
        {screen === "bridge" && <BridgeScreen />}
        {screen === "about" && <AboutScreen />}
      </main>

      <footer className="app__footer">
        <span>
          {liveActive
            ? "Live mode drives the repository's own CLI tools through the Webby bridge on the machine running it."
            : "Everything runs inside this tab: the simulator models a TeamTalk server, so no packets leave your browser."}
        </span>
        <span className="mono">v0.2.0-alpha-soft</span>
      </footer>
    </div>
  );
}
