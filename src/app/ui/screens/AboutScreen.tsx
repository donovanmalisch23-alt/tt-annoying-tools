import type { ReactElement } from "react";
import { Badge, Panel } from "../components";

const MAPPING: Array<[string, string, string]> = [
  ["tt_message_spammer.py", "Message sender", "Channel or private message sequences, counted exactly."],
  ["tt_spammer.py", "Login / logout cycles", "Repeated authenticated login cycles on one connection."],
  ["tt_leave_join_spammer.py", "Channel leave / join", "Leave and rejoin cycles in the configured channel."],
  ["tt_concurrent_bots.py", "Idle bots", "Parked clients occupying user slots, with a kick watchdog."],
  ["ttbot_the_offender.py", "Response bot", "Trigger reply only, allowlisted users, per-user cooldown."],
  ["tt_suite.py", "Combined suite", "Discovery, sequential operations, and concurrent bots."],
  ["tt_loic.py", "Local flood test", "Junk-load threads with before/during/after service probes."],
  ["tt_ramp.py", "Ramp / breaking point", "Geometric stages classified until the server stops coping."],
];

export function AboutScreen(): ReactElement {
  return (
    <div className="grid grid--about">
      <div className="stack">
        <Panel
          title="Two modes"
          subtitle="The same tools, either modelled in this tab or run for real."
          actions={<Badge tone="accent">alpha-soft</Badge>}
        >
          <ul className="steps">
            <li>
              <strong>Simulated</strong> (the default) runs every tool against an in-tab model of a
              TeamTalk server. Nothing leaves the browser, and the logic on show is the tools' own.
            </li>
            <li>
              <strong>Live server</strong> sends the run to the Webby bridge
              (<span className="mono">./run_webby.sh start</span>), which starts the repository's
              real <span className="mono">tt_*.py</span> tool as a child process and streams its
              output back. Credentials travel in the child's environment, never in its argv, and one
              run happens at a time.
            </li>
            <li>
              Live mode adds <strong>Bridge &amp; admin</strong>: sign in with the administrator
              credential to edit the allowlist file the bridge enforces — any file, anywhere, is
              fine; the bridge passes its exact path to the tools.
            </li>
          </ul>
        </Panel>

        <Panel
          title="What this is"
          subtitle="A web control panel for the TeamTalk Annoying Tools suite, with a live simulator."
          actions={<Badge tone="accent">alpha-soft</Badge>}
        >
          <p className="prose">
            Every tool from the Python suite is ported here as a browser app, running against an
            in-tab model of a TeamTalk server. The point is to be able to demonstrate, test and
            inspect the tools' actual logic — exact counts, kick resistance and recovery, discovery,
            load classification and the breaking-point verdict — without pointing anything at a real
            server or asking anyone for a network exception.
          </p>
          <p className="prose">
            The simulator implements a real command path: latency with jitter, command loss under
            load, flood protection that kicks a peer, a max-user cap, channel passwords, hidden
            channels, server-assigned user IDs that change on every login, and a load curve that
            saturates at a fixed number of junk threads. When a tool reports a reconnect, a retry, a
            degraded stage or a breaking point, that came from the tools' own code path.
          </p>
        </Panel>

        <Panel title="Desktop tool → this panel">
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>CLI</th>
                  <th>Panel</th>
                  <th>What it does here</th>
                </tr>
              </thead>
              <tbody>
                {MAPPING.map(([cli, panel, note]) => (
                  <tr key={cli}>
                    <td className="mono">{cli}</td>
                    <td>{panel}</td>
                    <td>{note}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Panel>

        <Panel title="Safety gates, kept intact">
          <ul className="steps">
            <li>
              <strong>Exact-host allowlist</strong> — the suite, idle bots and the ramp test refuse a
              host that is not listed on the Allowlist screen: the same gate the CLI applies to
              <span className="mono"> whitelist.txt</span>.
            </li>
            <li>
              <strong>Confirmation</strong> — every heavy tool needs an explicit confirm toggle
              before it will run, mirroring <span className="mono">--confirm</span>.
            </li>
            <li>
              <strong>Local-only flood</strong> — the flood test refuses any target that is not an
              address on this machine, exactly like <span className="mono">tt_loic.py</span>.
            </li>
            <li>
              <strong>Bounded retries</strong> — three consecutive failures against the same target
              give up cleanly, so a dead target cannot become a retry storm.
            </li>
            <li>
              <strong>Benign response bot</strong> — the desktop tool's automatic insults are not
              reproduced; this one answers one explicit trigger for allowlisted users only.
            </li>
          </ul>
        </Panel>
      </div>

      <div className="stack">
        <Panel title="Differences from the desktop suite">
          <ul className="steps">
            <li>
              <strong>No worker processes.</strong> The CLI forks workers to stay under the native
              select() file-descriptor ceiling. One browser tab cannot fork, so bots are threads and
              the caps are 128 idle bots / 64 concurrent suite bots, refused cleanly when exceeded.
            </li>
            <li>
              <strong>No numbered pickers.</strong> The interactive channel and recipient pickers are
              replaced by discovery output plus the parameter form. Recipients are still addressed by
              username, and still re-resolved on every send.
            </li>
            <li>
              <strong>Time scale.</strong> Waits are compressed by a shared clock so a seven-stage
              ramp is watchable. Every duration is scaled together, so ratios and verdicts hold.
            </li>
            <li>
              <strong>Password-protected channels are skipped with a warning</strong> during
              all-channels discovery rather than aborting the run.
            </li>
          </ul>
        </Panel>

        <Panel title="Honest limitations">
          <ul className="steps">
            <li>
              In simulated mode nothing leaves this tab. It is a model of a server, not a client for
              a real one — so a passing run here is evidence about the tools, not about your server.
              Live mode is the opposite: it is real, and it is your responsibility to run it only
              against a server you are allowed to test.
            </li>
            <li>
              The flood tools simulate thread counts against the load curve; they do not open TCP or
              UDP sockets, which a browser could not do anyway.
            </li>
            <li>
              The Python suite's own tests still live in <span className="mono">src/tests/</span> and
              cover the CLI, not this panel.
            </li>
          </ul>
        </Panel>

        <Panel title="Credits">
          <p className="prose">
            Desktop tools credited <strong>blindelectron</strong>, <strong>RD-Productions</strong>,{" "}
            <strong>Simpter</strong> and <strong>Patrick Wilson</strong>. TeamTalk and the TeamTalk
            SDK are by <strong>BearWare.dk</strong>.
          </p>
        </Panel>
      </div>
    </div>
  );
}
