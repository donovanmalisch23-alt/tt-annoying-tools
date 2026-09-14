import { useEffect, useMemo, useState } from "react";
import type { ReactElement } from "react";
import { liveStore, useLive } from "../../live/store";
import { bridgeCandidates, describeBase, DEFAULT_BRIDGE_PORT } from "../../live/api";
import { useConfig } from "../../core/store";
import { Whitelist } from "../../core/types";
import { Badge, Button, Field, Panel, Stat, TextArea, TextInput } from "../components";

/** The same host shape the bridge accepts (hostname, IP literal, or [IPv6]). */
const HOST_RE = /^\[?[A-Za-z0-9_.:-]+\]?$/;

interface LineProblem {
  line: number;
  text: string;
}

function lintAllowlist(raw: string): { entries: string[]; problems: LineProblem[] } {
  const problems: LineProblem[] = [];
  const entries: string[] = [];
  raw.split(/\r?\n/).forEach((line, index) => {
    const stripped = line.split("#")[0].trim();
    if (!stripped) return;
    if (!HOST_RE.test(stripped)) {
      problems.push({ line: index + 1, text: stripped });
      return;
    }
    const normalized = Whitelist.normalize(stripped);
    if (normalized && !entries.includes(normalized)) entries.push(normalized);
  });
  return { entries, problems };
}

function StatusBadge(): ReactElement {
  const live = useLive();
  if (live.mode !== "live") return <Badge tone="muted">simulated mode</Badge>;
  switch (live.status) {
    case "online":
      return <Badge tone="ok">bridge online</Badge>;
    case "connecting":
      return <Badge tone="warn">connecting…</Badge>;
    case "offline":
      return <Badge tone="danger">bridge unreachable</Badge>;
    default:
      return <Badge tone="muted">not connected</Badge>;
  }
}

export function BridgeScreen(): ReactElement {
  const live = useLive();
  const config = useConfig();

  const [address, setAddress] = useState(live.baseInput);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [raw, setRaw] = useState("");
  const [hostToAdd, setHostToAdd] = useState("");
  const [dirty, setDirty] = useState(false);

  const online = live.status === "online";
  const signedIn = live.signedIn && online;

  // Pull the bridge's copy of the file into the editor whenever it reloads.
  useEffect(() => {
    if (live.whitelist) {
      setRaw(live.whitelist.raw ?? `${live.whitelist.entries.join("\n")}\n`);
      setDirty(false);
    }
  }, [live.whitelist]);

  useEffect(() => {
    if (live.mode === "sim") setAddress(live.baseInput);
  }, [live.mode, live.baseInput]);

  const linted = useMemo(() => lintAllowlist(raw), [raw]);
  const savedCount = live.whitelist?.count ?? 0;

  const signIn = async (): Promise<void> => {
    setBusy(true);
    try {
      await liveStore.login(username.trim(), password);
      setPassword("");
    } catch {
      /* the store surfaces the message */
    } finally {
      setBusy(false);
    }
  };

  const save = async (force: boolean): Promise<void> => {
    setBusy(true);
    try {
      await liveStore.saveWhitelist(raw, force);
      setDirty(false);
    } catch {
      /* the store surfaces the message */
    } finally {
      setBusy(false);
    }
  };

  const addHost = (host: string): void => {
    const value = Whitelist.normalize(host);
    if (!value) return;
    const lines = raw.split(/\r?\n/).filter((line) => line.trim() !== "");
    if (linted.entries.includes(value)) return;
    setRaw(`${[...lines, value].join("\n")}\n`);
    setDirty(true);
    setHostToAdd("");
  };

  return (
    <div className="grid grid--settings">
      <div className="stack">
        <Panel
          title="Webby bridge"
          subtitle="The local process that runs the real tools and owns the allowlist file."
          actions={<StatusBadge />}
        >
          {live.mode === "sim" ? (
            <div className="alert alert--warn">
              <p>
                The panel is in <strong>simulated mode</strong>, so runs act on the in-tab server model.
                Switch the header toggle to <strong>Live server</strong> to drive the real CLI tools.
              </p>
              <Button size="sm" onClick={() => liveStore.setMode("live")}>
                Go live
              </Button>
            </div>
          ) : null}

          <div className="form-grid form-grid--2">
            <Field
              label="Bridge address"
              help={`Blank means this page's own origin — correct when the panel was served by ./run_webby.sh start (port ${DEFAULT_BRIDGE_PORT}).`}
            >
              <TextInput
                value={address}
                onChange={(value) => {
                  setAddress(value);
                  liveStore.setBaseInput(value);
                }}
                placeholder={`http://127.0.0.1:${DEFAULT_BRIDGE_PORT}`}
              />
            </Field>
            <Field label="Connect" help="Tries the address above, then the usual local ports.">
              <div className="row">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy}
                  onClick={() => void liveStore.connect(address)}
                >
                  {busy ? "Trying…" : "Connect"}
                </Button>
                <Button size="sm" variant="ghost" onClick={() => void liveStore.refresh()}>
                  Refresh
                </Button>
              </div>
            </Field>
          </div>

          <p className="prose">
            A browser cannot open a raw TCP connection to a TeamTalk server, so live mode needs the
            bridge running on the machine that can reach it:
          </p>
          <pre className="code">./run_webby.sh start</pre>
          <p className="prose">
            That same command builds the panel and serves it from the bridge, which is the simplest
            setup: the page and the API share one origin.
          </p>

          {live.status === "offline" && (
            <div className="stack">
              <p className="alert alert--danger">{live.error ?? "The bridge did not answer."}</p>
              <p className="prose">Addresses this panel will try:</p>
              <div className="row row--wrap">
                {bridgeCandidates().map((candidate) => (
                  <Button
                    key={candidate || "origin"}
                    size="sm"
                    variant="ghost"
                    title={`Try ${candidate || "this origin"}`}
                    onClick={() => {
                      setAddress(candidate);
                      liveStore.setBaseInput(candidate);
                      void liveStore.connect(candidate);
                    }}
                  >
                    {describeBase(candidate)}
                  </Button>
                ))}
              </div>
              <p className="prose">
                If the sandbox or container only exposes one port, run the bridge on your own machine
                and open <span className="mono">{`http://127.0.0.1:${DEFAULT_BRIDGE_PORT}/`}</span> —
                the panel loads from the bridge in that case.
              </p>
            </div>
          )}
        </Panel>

        {online && live.health && (
          <Panel title="Bridge details" subtitle={live.health.config.repo_root}>
            <div className="stats">
              <Stat label="Version" value={live.health.version} />
              <Stat
                label="Allowlist"
                value={`${savedCount} host(s)`}
                hint={live.health.config.whitelist_path}
              />
              <Stat
                label="Admin"
                value={live.health.admin.configured ? (live.health.admin.username ?? "set") : "not set"}
                hint={`source: ${live.health.admin.source}`}
              />
              <Stat
                label="SDK"
                value={
                  live.health.config.sdk_python_present && live.health.config.sdk_library_present
                    ? "present"
                    : "missing"
                }
                tone={
                  live.health.config.sdk_python_present && live.health.config.sdk_library_present
                    ? "ok"
                    : "danger"
                }
                hint="sdk/TeamTalk5.py + libTeamTalk5.*"
              />
              <Stat
                label="Panel build"
                value={live.health.config.dist_built ? "built" : "not built"}
                tone={live.health.config.dist_built ? "ok" : "warn"}
                hint={live.health.config.dist_dir}
              />
              <Stat
                label="Runs"
                value={live.health.config.require_admin_for_runs ? "admin only" : "open"}
                hint={
                  live.health.config.max_run_seconds > 0
                    ? `cap ${live.health.config.max_run_seconds}s`
                    : "no time cap"
                }
              />
            </div>
            <p className="prose mono">{live.health.config.python}</p>
          </Panel>
        )}

        {online && (
          <Panel title="Active run" subtitle="One run at a time: the bridge refuses a second one.">
            {live.run ? (
              <div className="stack">
                <div className="row row--wrap">
                  <Badge
                    tone={
                      live.run.state === "running"
                        ? "accent"
                        : live.run.state === "finished"
                          ? "ok"
                          : live.run.state === "failed"
                            ? "danger"
                            : "warn"
                    }
                  >
                    {live.run.state}
                  </Badge>
                  <span className="mono">{live.run.script}</span>
                  <span className="mono">{live.run.host}</span>
                  {live.run.note && <span className="prose">{live.run.note}</span>}
                </div>
                <pre className="code">{live.run.argv.join(" ")}</pre>
                {live.run.error && <p className="alert alert--danger">{live.run.error}</p>}
                <div className="row">
                  <Button
                    size="sm"
                    variant="danger"
                    disabled={live.run.state !== "running"}
                    onClick={() => void liveStore.stopRun()}
                  >
                    Stop run
                  </Button>
                  <Button size="sm" variant="ghost" onClick={() => void liveStore.clearRun()}>
                    Clear
                  </Button>
                </div>
                <p className="prose">
                  The Console tab shows this run's output as it arrives.
                </p>
              </div>
            ) : (
              <p className="prose">
                No run yet in this bridge session. Start one from the Tools tab.
              </p>
            )}
          </Panel>
        )}
      </div>

      <div className="stack">
        <Panel
          title="Administrator"
          subtitle="Editing the allowlist is the one privileged action."
          actions={
            signedIn ? (
              <Button size="sm" variant="ghost" onClick={() => void liveStore.logout()}>
                Sign out
              </Button>
            ) : (
              <Badge tone="warn">signed out</Badge>
            )
          }
        >
          {!online ? (
            <p className="prose">Connect to the bridge first.</p>
          ) : signedIn ? (
            <div className="stack">
              <p className="prose">
                Signed in as <strong>{live.username}</strong>. You can edit the allowlist below.
              </p>
              <p className="prose">
                The session is an in-memory token with an expiry; restarting the bridge signs
                everyone out.
              </p>
            </div>
          ) : live.health && !live.health.admin.configured ? (
            <div className="stack">
              <p className="alert alert--warn">
                No admin credential is configured on this bridge yet, so there is nobody to sign in
                as. Create one — it is stored as a PBKDF2 hash, never in clear text:
              </p>
              <pre className="code">./run_webby.sh admin set --username admin</pre>
              <p className="prose">
                Add <span className="mono">--generate</span> to have a strong password printed once,
                or set <span className="mono">WEBBY_ADMIN_PASSWORD</span> before starting.
              </p>
            </div>
          ) : (
            <div className="stack">
              <div className="form-grid form-grid--2">
                <Field label="Username">
                  <TextInput
                    value={username}
                    onChange={setUsername}
                    placeholder={live.health?.admin.username ?? "admin"}
                  />
                </Field>
                <Field label="Password">
                  <TextInput type="password" value={password} onChange={setPassword} />
                </Field>
              </div>
              <div className="row">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || !username.trim() || !password}
                  onClick={() => void signIn()}
                >
                  {busy ? "Signing in…" : "Sign in"}
                </Button>
              </div>
              {live.error && <p className="alert alert--danger">{live.error}</p>}
              <p className="prose">
                Five failed attempts from one address within a minute are throttled.
              </p>
            </div>
          )}
        </Panel>

        <Panel
          title="Allowlist file"
          subtitle={
            online && live.whitelist
              ? live.whitelist.path
              : "The exact-host list every gated tool checks."
          }
          tone="danger"
          actions={
            <div className="row">
              <Badge tone={savedCount > 0 ? "ok" : "danger"}>
                {savedCount} saved
              </Badge>
              {dirty && <Badge tone="warn">unsaved</Badge>}
            </div>
          }
        >
          {!online ? (
            <p className="prose">Connect to the bridge to see the file it enforces.</p>
          ) : !signedIn ? (
            <div className="stack">
              <p className="prose">
                Sign in above to edit this file. Until then it is read-only here — and it is
                enforced by the tools themselves, not by this panel.
              </p>
              <div className="row row--wrap">
                {savedCount === 0 ? (
                  <span className="prose">The file is empty, so gated tools refuse to run.</span>
                ) : (
                  live.whitelist?.entries.map((entry) => (
                    <span key={entry} className="tag mono">
                      {entry}
                    </span>
                  ))
                )}
              </div>
            </div>
          ) : (
            <div className="stack">
              <p className="prose">
                One host per line; <span className="mono">#</span> starts a comment. The bridge
                validates every entry before writing, and replaces the file atomically.
              </p>
              <TextArea value={raw} onChange={(value) => { setRaw(value); setDirty(true); }} rows={10} mono />

              <div className="row row--wrap">
                <span className="gate__label">Parsed</span>
                {linted.entries.length === 0 ? (
                  <Badge tone="danger">no entries</Badge>
                ) : (
                  linted.entries.map((entry) => (
                    <span key={entry} className="tag mono">
                      {entry}
                    </span>
                  ))
                )}
              </div>

              {linted.problems.length > 0 && (
                <p className="alert alert--danger">
                  The bridge will refuse this file:{" "}
                  {linted.problems.map((problem) => `line ${problem.line} (${problem.text})`).join(", ")}
                </p>
              )}

              <div className="row row--wrap">
                <Button
                  variant="primary"
                  size="sm"
                  disabled={busy || linted.problems.length > 0}
                  onClick={() => void save(false)}
                >
                  {busy ? "Saving…" : "Save allowlist"}
                </Button>
                <Button
                  size="sm"
                  variant="ghost"
                  disabled={busy}
                  onClick={() => void liveStore.reloadWhitelist()}
                >
                  Reload from disk
                </Button>
                {live.error && /changed on disk/i.test(live.error) && (
                  <Button size="sm" variant="danger" disabled={busy} onClick={() => void save(true)}>
                    Overwrite anyway
                  </Button>
                )}
              </div>

              <div className="form-grid form-grid--2">
                <Field label="Add a host" help="Normalized to lowercase, no brackets, no trailing dot.">
                  <div className="row">
                    <TextInput
                      value={hostToAdd}
                      onChange={setHostToAdd}
                      placeholder="test.example"
                    />
                    <Button size="sm" onClick={() => addHost(hostToAdd)}>
                      Add
                    </Button>
                  </div>
                </Field>
                <Field label="Current target" help="From the Server & allowlist tab.">
                  <div className="row">
                    <span className="mono">{config.host}</span>
                    <Button size="sm" variant="ghost" onClick={() => addHost(config.host)}>
                      Add it
                    </Button>
                  </div>
                </Field>
              </div>

              {live.note && <p className="alert alert--ok">{live.note}</p>}
              {live.error && !/changed on disk/i.test(live.error) && (
                <p className="alert alert--danger">{live.error}</p>
              )}
            </div>
          )}
        </Panel>

        <Panel title="How the gates line up" subtitle="Three checks, all independent.">
          <ol className="prose">
            <li>
              <strong>This panel</strong> disables Run when the target is not in the list it last
              read.
            </li>
            <li>
              <strong>The bridge</strong> re-checks the list on disk and refuses the request before
              spawning anything.
            </li>
            <li>
              <strong>The tool itself</strong> reads the same file (the bridge passes its exact path
              with <span className="mono">--whitelist</span>) and refuses again.
            </li>
          </ol>
          <p className="prose">
            The flood tool adds a fourth gate: it only ever runs against the machine it is on, and
            the bridge checks that too.
          </p>
        </Panel>
      </div>
    </div>
  );
}
