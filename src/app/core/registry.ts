import { TeamTalkConfigError } from "./types";

export type FieldKind = "text" | "secret" | "int" | "number" | "toggle" | "choice" | "multiline";

export interface FieldSpec {
  key: string;
  label: string;
  kind: FieldKind;
  help?: string;
  default: string;
  choices?: string[];
}

export interface ToolSpec {
  id: string;
  title: string;
  tagline: string;
  description: string;
  fields: FieldSpec[];
  /** Target host must appear in the allowlist before this tool will connect. */
  requiresWhitelist: boolean;
  /** The UI asks for an explicit confirmation before starting. */
  requiresConfirm: boolean;
  /** Part of the gentle surface rather than the heavy load tools. */
  soft: boolean;
}

export type Values = Record<string, string>;

export const TOOL_IDS = {
  message: "message-sender",
  login: "login-cycles",
  leaveJoin: "leave-join",
  idleBots: "idle-bots",
  responseBot: "response-bot",
  suite: "suite",
  loic: "loic",
  ramp: "ramp",
} as const;

export const TOOL_SPECS: ToolSpec[] = [
  {
    id: TOOL_IDS.message,
    title: "Message sender",
    tagline: "Send a channel or private message sequence",
    description:
      "Sends a configurable channel or private text message sequence through one session, " +
      "with kick resistance and exact counts: an interrupted send is retried after the " +
      "reconnect and never counted twice.",
    fields: [
      { key: "target", label: "Target", kind: "choice", default: "channel", choices: ["channel", "private"] },
      { key: "message", label: "Message text", kind: "multiline", default: "Oh Yeah!", help: "Up to 4096 UTF-8 bytes." },
      { key: "users", label: "Recipients (usernames)", kind: "text", default: "", help: "Comma-separated; used for the private target." },
      { key: "count", label: "Messages per recipient", kind: "int", default: "3" },
      { key: "interval_ms", label: "Delay between messages (ms)", kind: "number", default: "50" },
      { key: "wait", label: "Startup wait (s)", kind: "number", default: "0" },
    ],
    requiresWhitelist: false,
    requiresConfirm: false,
    soft: true,
  },
  {
    id: TOOL_IDS.login,
    title: "Login / logout cycles",
    tagline: "Repeat login and logout on one connection",
    description:
      "Keeps one connection open while it repeats authenticated login/logout cycles without " +
      "joining a channel. Only completed cycles count.",
    fields: [
      { key: "cycles", label: "Cycles", kind: "int", default: "5" },
      { key: "interval_ms", label: "Delay between cycles (ms)", kind: "number", default: "200" },
      { key: "wait", label: "Startup wait (s)", kind: "number", default: "0" },
    ],
    requiresWhitelist: false,
    requiresConfirm: false,
    soft: true,
  },
  {
    id: TOOL_IDS.leaveJoin,
    title: "Channel leave / join",
    tagline: "Repeat leave and rejoin cycles",
    description:
      "Joins the configured channel, then leaves and rejoins it for each requested cycle. " +
      "Only completed pairs count.",
    fields: [
      { key: "cycles", label: "Cycles", kind: "int", default: "5" },
      { key: "interval_ms", label: "Delay between leave and join (ms)", kind: "number", default: "200" },
      { key: "wait", label: "Startup wait (s)", kind: "number", default: "50" },
    ],
    requiresWhitelist: false,
    requiresConfirm: false,
    soft: true,
  },
  {
    id: TOOL_IDS.responseBot,
    title: "Response bot",
    tagline: "Trigger-based benign replies",
    description:
      "Listens in the configured channel and replies only to an explicit trigger, from " +
      "allowlisted users, with a per-user cooldown keyed on the username. The default reply " +
      "is intentionally benign — the desktop tool's automatic insults are not reproduced.",
    fields: [
      { key: "trigger", label: "Trigger prefix", kind: "text", default: "!hello" },
      { key: "response", label: "Response template", kind: "text", default: "Hi {username}, thanks for your message!", help: "Placeholders: {username}, {user_id}, {message}." },
      { key: "allow_users", label: "Allowlisted usernames", kind: "text", default: "", help: "Comma-separated; or enable Allow any user." },
      { key: "allow_all", label: "Allow any user", kind: "toggle", default: "false" },
      { key: "cooldown", label: "Per-user cooldown (s)", kind: "number", default: "30", help: "Minimum 5." },
      { key: "max_responses", label: "Max responses (0 = unlimited)", kind: "int", default: "100" },
    ],
    requiresWhitelist: false,
    requiresConfirm: false,
    soft: true,
  },
  {
    id: TOOL_IDS.idleBots,
    title: "Idle bots",
    tagline: "Park logged-in clients on the server",
    description:
      "Launches idle bots that connect, log in, join the channel and sit there occupying " +
      "server slots. The simulator enforces a max-user cap, so you can watch bots get " +
      "refused once the server is full.",
    fields: [
      { key: "count", label: "Idle bots", kind: "int", default: "4", help: "1 to 128." },
      { key: "start_delay_ms", label: "Delay between launches (ms)", kind: "int", default: "120" },
      { key: "connect_attempts", label: "Connect attempts per bot", kind: "int", default: "3" },
    ],
    requiresWhitelist: true,
    requiresConfirm: true,
    soft: false,
  },
  {
    id: TOOL_IDS.suite,
    title: "Combined suite",
    tagline: "Discover targets, then run every selected test",
    description:
      "Discovers channels and users, then runs login cycles, join/leave cycles, channel " +
      "messages and private messages. Concurrent mode splits the work across dedicated bots, " +
      "each on its own connection.",
    fields: [
      { key: "all_channels", label: "All discovered channels", kind: "toggle", default: "true" },
      { key: "all_users", label: "All discovered users", kind: "toggle", default: "true" },
      { key: "users", label: "Recipients (usernames)", kind: "text", default: "", help: "Used when All users is off." },
      { key: "channel_message", label: "Channel message", kind: "text", default: "" },
      { key: "private_message", label: "Private message", kind: "text", default: "" },
      { key: "message_count", label: "Messages per target", kind: "int", default: "1" },
      { key: "join_leave_cycles", label: "Join/leave cycles", kind: "int", default: "0" },
      { key: "login_cycles", label: "Login/logout cycles", kind: "int", default: "0" },
      { key: "interval", label: "Delay between operations (s)", kind: "number", default: "0.2" },
      { key: "concurrent", label: "Concurrent bots", kind: "toggle", default: "false" },
      { key: "churn_bots", label: "Churn bots", kind: "int", default: "0" },
      { key: "churn_cycles", label: "Cycles per churn bot", kind: "int", default: "10" },
      { key: "bot_per_channel", label: "One bot per channel", kind: "toggle", default: "false" },
      { key: "bot_per_user", label: "One bot per user", kind: "toggle", default: "false" },
      { key: "sweep_interval", label: "New-joiner sweep interval (s)", kind: "number", default: "0.5" },
      { key: "dry_run", label: "Discover only (dry run)", kind: "toggle", default: "false" },
    ],
    requiresWhitelist: true,
    requiresConfirm: true,
    soft: false,
  },
  {
    id: TOOL_IDS.loic,
    title: "Local flood test",
    tagline: "LOIC-style TCP/UDP load, local targets only",
    description:
      "Ramps junk-load threads at the target and measures the impact with a before / during / " +
      "after service probe pair, then prints a plain-language verdict. The local-only gate " +
      "refuses anything that is not a loopback or private address, exactly like the CLI.",
    fields: [
      { key: "mode", label: "Mode", kind: "choice", default: "both", choices: ["both", "tcp", "udp"] },
      { key: "threads", label: "Threads per mode", kind: "int", default: "8", help: "1 to 64." },
      { key: "duration", label: "Duration (s)", kind: "number", default: "10", help: "Maximum 60." },
      { key: "probe", label: "Run service probes", kind: "toggle", default: "true" },
      { key: "probe_channel", label: "Probe channel", kind: "text", default: "/Lobby" },
    ],
    requiresWhitelist: false,
    requiresConfirm: true,
    soft: false,
  },
  {
    id: TOOL_IDS.ramp,
    title: "Ramp / breaking point",
    tagline: "Step the load up until the server stops coping",
    description:
      "Ramps the load in geometric stages, classifying each stage healthy, degraded or broken, " +
      "and reports the breaking point. Any host in the allowlist is a legal target.",
    fields: [
      { key: "start_threads", label: "Start threads", kind: "int", default: "1" },
      { key: "ramp_factor", label: "Ramp factor", kind: "int", default: "2" },
      { key: "max_threads", label: "Max threads", kind: "int", default: "64", help: "1 to 1024." },
      { key: "stage_duration", label: "Seconds per stage", kind: "number", default: "6", help: "1 to 60." },
      { key: "mode", label: "Mode", kind: "choice", default: "both", choices: ["both", "tcp", "udp"] },
      { key: "probe_channel", label: "Probe channel", kind: "text", default: "/Lobby" },
      { key: "dry_run", label: "Print the plan only (dry run)", kind: "toggle", default: "false" },
    ],
    requiresWhitelist: true,
    requiresConfirm: true,
    soft: false,
  },
];

export function toolSpec(id: string): ToolSpec {
  const spec = TOOL_SPECS.find((candidate) => candidate.id === id);
  if (!spec) throw new TeamTalkConfigError(`Unknown tool '${id}'.`);
  return spec;
}

export function defaultValues(id: string): Values {
  const values: Values = {};
  for (const field of toolSpec(id).fields) values[field.key] = field.default;
  return values;
}

/** Accepts `1_000` and `10,999` like the CLI's comma_int argparse type. */
export function parseLooseInt(value: string): number | null {
  const cleaned = value.trim().replace(/[_,]/g, "");
  if (!/^-?\d+$/.test(cleaned)) return null;
  const parsed = Number(cleaned);
  return Number.isFinite(parsed) ? parsed : null;
}

export const str = (values: Values, key: string, fallback = ""): string => {
  const value = values[key];
  return value && value !== "" ? value : fallback;
};

export const bool = (values: Values, key: string): boolean =>
  (values[key] ?? "false").toLowerCase() === "true";

export const int = (values: Values, key: string, fallback: number): number => {
  const raw = values[key]?.trim();
  if (!raw) return fallback;
  const parsed = parseLooseInt(raw);
  if (parsed === null) throw new TeamTalkConfigError(`'${raw}' is not a whole number.`);
  return parsed;
};

export const num = (values: Values, key: string, fallback: number): number => {
  const raw = values[key]?.trim();
  if (!raw) return fallback;
  const parsed = Number(raw);
  if (!Number.isFinite(parsed)) throw new TeamTalkConfigError(`'${raw}' is not a number.`);
  return parsed;
};

export const parseUserList = (raw: string): string[] =>
  Array.from(
    new Set(
      raw
        .split(",")
        .map((name) => name.trim())
        .filter((name) => name.length > 0),
    ),
  );
