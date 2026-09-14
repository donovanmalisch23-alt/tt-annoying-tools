/** Base error for anything a tool raises, mirroring tt_teamtalk.TeamTalkError. */
export class TeamTalkError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "TeamTalkError";
  }
}

/** A missing or invalid setting, mirroring TeamTalkConfigurationError. */
export class TeamTalkConfigError extends TeamTalkError {
  constructor(message: string) {
    super(message);
    this.name = "TeamTalkConfigError";
  }
}

/** Raised when a run is stopped by the user. */
export class ToolCancelledError extends Error {
  constructor() {
    super("Run cancelled");
    this.name = "ToolCancelledError";
  }
}

export interface ConnectionConfig {
  host: string;
  tcpPort: number;
  udpPort: number;
  username: string;
  password: string;
  nickname: string;
  clientName: string;
  encrypted: boolean;
  channelId: number | null;
  channelPath: string | null;
  channelPassword: string;
  commandTimeoutSec: number;
  kickResistance: boolean;
  reconnectDelaySec: number;
}

export const DEFAULT_CONFIG: ConnectionConfig = {
  host: "127.0.0.1",
  tcpPort: 10333,
  udpPort: 10333,
  username: "",
  password: "",
  nickname: "tt-web-client",
  clientName: "TT Annoying Tools Web",
  encrypted: false,
  channelId: null,
  channelPath: "/Lobby",
  channelPassword: "",
  commandTimeoutSec: 15,
  kickResistance: true,
  reconnectDelaySec: 3.5,
};

/** Throws TeamTalkConfigError for anything the tools cannot work with. */
export function validateConfig(config: ConnectionConfig): ConnectionConfig {
  const host = config.host.trim();
  if (!host) throw new TeamTalkConfigError("Server host is required; refusing to guess a server.");
  if (!(config.tcpPort >= 1 && config.tcpPort <= 65535)) {
    throw new TeamTalkConfigError("TCP port must be between 1 and 65535.");
  }
  if (!(config.udpPort >= 1 && config.udpPort <= 65535)) {
    throw new TeamTalkConfigError("UDP port must be between 1 and 65535.");
  }
  if (config.commandTimeoutSec <= 0) {
    throw new TeamTalkConfigError("Command timeout must be greater than zero.");
  }
  if (config.reconnectDelaySec < 0) {
    throw new TeamTalkConfigError("Reconnect delay cannot be negative.");
  }
  if (config.channelId !== null && config.channelId < 0) {
    throw new TeamTalkConfigError("Channel ID cannot be negative.");
  }
  const trimmedPath = config.channelPath?.trim();
  const channelPath = trimmedPath
    ? trimmedPath.startsWith("/")
      ? trimmedPath
      : `/${trimmedPath}`
    : null;
  return { ...config, host, channelPath };
}

export interface ChannelInfo {
  id: number;
  parentId: number;
  name: string;
  path: string;
  passwordRequired: boolean;
  hidden: boolean;
}

export interface UserInfo {
  id: number;
  nickname: string;
  username: string;
  channelId: number;
  channelPath: string;
}

export function displayName(user: UserInfo): string {
  return user.nickname || user.username || `user ${user.id}`;
}

export interface TextEvent {
  type: number;
  fromUserId: number;
  /** Account name; empty for an anonymous login. */
  fromUsername: string;
  /** Display name, used as the identity fallback when there is no username. */
  fromNickname: string;
  toUserId: number;
  channelId: number;
  text: string;
  more: boolean;
}

/** Exact-host allowlist, identical in behaviour to the desktop suite's gate. */
export const Whitelist = {
  normalize(host: string): string {
    let normalized = host.trim().toLowerCase().replace(/\.+$/, "");
    if (normalized.startsWith("[") && normalized.endsWith("]")) {
      normalized = normalized.slice(1, -1);
    }
    return normalized;
  },

  parse(text: string): string[] {
    const entries = text
      .split(/\r?\n/)
      .map((line) => line.split("#")[0].trim())
      .filter((line) => line.length > 0)
      .map((line) => Whitelist.normalize(line));
    return Array.from(new Set(entries));
  },

  isAllowed(host: string, entries: string[]): boolean {
    const normalized = Whitelist.normalize(host);
    return entries.some((entry) => Whitelist.normalize(entry) === normalized);
  },

  requireAllowed(host: string, entries: string[]): void {
    if (entries.length === 0) {
      throw new TeamTalkConfigError(
        "The server allowlist is empty; add one hostname or IP per line first.",
      );
    }
    if (!Whitelist.isAllowed(host, entries)) {
      throw new TeamTalkConfigError(
        `'${host}' is not in the server allowlist. Add it before running this test.`,
      );
    }
  },
};
