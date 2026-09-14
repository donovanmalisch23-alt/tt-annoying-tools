import type { ChannelInfo, TextEvent, UserInfo } from "../core/types";
import { TeamTalkError } from "../core/types";
import { scaled, simNow, sleep } from "./clock";

export interface SimChannel {
  id: number;
  parentId: number;
  name: string;
  password: string;
  hidden: boolean;
}

export interface SimUser {
  id: number;
  username: string;
  nickname: string;
  channelId: number;
  sessionId: string;
}

export type SimEvent =
  | { kind: "text"; event: TextEvent }
  | { kind: "con_lost"; reason: string }
  | { kind: "user_joined"; user: SimUser }
  | { kind: "user_left"; user: SimUser };

/** Anything the server can push events to — a SimSession at runtime. */
export interface SimPeer {
  readonly id: string;
  receive(event: SimEvent): void;
}

export interface ServerSettings {
  online: boolean;
  latencyMs: number;
  jitterMs: number;
  protectionEnabled: boolean;
  protectionBurst: number;
  protectionWindowMs: number;
  maxUsers: number;
  autoJoin: boolean;
}

export const DEFAULT_SETTINGS: ServerSettings = {
  online: true,
  latencyMs: 10,
  jitterMs: 6,
  protectionEnabled: true,
  protectionBurst: 14,
  protectionWindowMs: 1000,
  maxUsers: 40,
  autoJoin: false,
};

/** Threads of junk load at which the simulated server is fully saturated. */
export const BREAK_THREADS = 40;

const CHANNEL_ROOT = 1;

const AUTO_NAMES = [
  "dana", "erin", "frank", "gina", "hugo", "iris", "jack", "kira",
  "liam", "mona", "nate", "opal", "pete", "quinn", "rosa", "sam",
];

/**
 * A TeamTalk server model: channel tree, roster, command latency, flood
 * protection and a load curve. Every tool in the panel runs against this, so
 * the panel's own logic — exact counts, kick resistance, probe classification
 * and the ramp verdict — is genuinely exercised rather than faked.
 */
export class SimServer {
  settings: ServerSettings = { ...DEFAULT_SETTINGS };
  channels: SimChannel[] = [];
  users = new Map<number, SimUser>();

  private peers = new Map<string, SimPeer>();
  private nextUserId = 1;
  private nextChannelId = 2;
  private flooders = new Map<string, number>();
  private commandTimes = new Map<string, number[]>();
  private autoTimer: number | null = null;

  constructor() {
    this.reset();
  }

  reset(): void {
    this.settings = { ...DEFAULT_SETTINGS };
    this.users.clear();
    this.peers.clear();
    this.flooders.clear();
    this.commandTimes.clear();
    this.nextUserId = 1;
    this.nextChannelId = 2;
    this.channels = [];
    this.addChannel(CHANNEL_ROOT, 0, "Root", "", false);
    const lobby = this.addChannel(this.nextChannelId++, CHANNEL_ROOT, "Lobby", "", false);
    this.addChannel(this.nextChannelId++, lobby, "Games", "", false);
    const music = this.addChannel(this.nextChannelId++, CHANNEL_ROOT, "Music", "", false);
    this.addChannel(this.nextChannelId++, music, "Requests", "", false);
    this.addChannel(this.nextChannelId++, CHANNEL_ROOT, "Ops", "secret", false);
    this.addChannel(this.nextChannelId++, CHANNEL_ROOT, "Backstage", "", true);
    this.seedUsers();
  }

  // ----- setup helpers ---------------------------------------------------- //

  private addChannel(
    id: number,
    parentId: number,
    name: string,
    password: string,
    hidden: boolean,
  ): number {
    this.channels.push({ id, parentId, name, password, hidden });
    return id;
  }

  private seedUsers(): void {
    const seed: Array<[string, string, number]> = [
      ["amy", "Amy", 2],
      ["bobby", "Bobby", 3],
      ["carol", "Carol", 2],
    ];
    for (const [username, nickname, channelId] of seed) {
      const id = this.nextUserId++;
      this.users.set(id, { id, username, nickname, channelId, sessionId: `builtin-${id}` });
    }
  }

  // ----- registry --------------------------------------------------------- //

  registerPeer(peer: SimPeer): void {
    this.peers.set(peer.id, peer);
  }

  unregisterPeer(peerId: string): void {
    this.peers.delete(peerId);
    this.flooders.delete(peerId);
    this.commandTimes.delete(peerId);
    // Any user this peer owned leaves with it.
    for (const [id, user] of Array.from(this.users.entries())) {
      if (user.sessionId === peerId) this.users.delete(id);
    }
  }

  private deliver(sessionId: string, event: SimEvent): void {
    this.peers.get(sessionId)?.receive(event);
  }

  // ----- load model ------------------------------------------------------- //

  registerFlood(sessionId: string, threads: number): void {
    this.flooders.set(sessionId, threads);
  }

  unregisterFlood(sessionId: string): void {
    this.flooders.delete(sessionId);
  }

  get floodThreads(): number {
    let total = 0;
    this.flooders.forEach((threads) => {
      total += threads;
    });
    return total;
  }

  /** 0 = idle, 1 = saturated. */
  get load(): number {
    return Math.min(1, this.floodThreads / BREAK_THREADS);
  }

  get latencyMultiplier(): number {
    return 1 + this.load * 6;
  }

  /** Probability that a command is lost under load. */
  get lossProbability(): number {
    const load = this.load;
    if (load <= 0.55) return 0;
    return Math.min(0.95, ((load - 0.55) / 0.45) * 0.95);
  }

  get saturated(): boolean {
    return this.load >= 0.98;
  }

  // ----- command plumbing ------------------------------------------------- //

  /** Latency, jitter and loss for one command; rejects when the command is lost. */
  async command(action: string): Promise<void> {
    if (!this.settings.online) {
      throw new TeamTalkError(`${action} failed: the server is offline.`);
    }
    const base = this.settings.latencyMs * this.latencyMultiplier;
    const jitter = this.settings.jitterMs * (1 + this.load * 5);
    await sleep(base + Math.random() * jitter);
    if (Math.random() < this.lossProbability) {
      throw new TeamTalkError(`${action} failed: command lost while the server was saturated.`);
    }
  }

  /**
   * Flood protection: too many commands from one peer inside the window and
   * the server kicks that peer. This is what exercises kick resistance.
   */
  noteCommand(peerId: string): void {
    if (!this.settings.protectionEnabled) return;
    // Simulated time, so the window means the same rate at any speed setting.
    const now = simNow();
    const window = this.settings.protectionWindowMs;
    const times = (this.commandTimes.get(peerId) ?? []).filter((t) => now - t < window);
    times.push(now);
    this.commandTimes.set(peerId, times);
    if (times.length > this.settings.protectionBurst) {
      this.commandTimes.set(peerId, []);
      this.kick(peerId, "flood protection triggered");
    }
  }

  kick(sessionId: string, reason: string): void {
    let kicked = false;
    for (const [id, user] of Array.from(this.users.entries())) {
      if (user.sessionId === sessionId) {
        this.users.delete(id);
        kicked = true;
      }
    }
    if (kicked) {
      this.deliver(sessionId, { kind: "con_lost", reason });
    }
  }

  kickAll(): void {
    for (const peerId of Array.from(this.peers.keys())) {
      this.kick(peerId, "operator kicked everyone");
    }
  }

  // ----- session operations ---------------------------------------------- //

  login(sessionId: string, nickname: string, username: string): SimUser {
    if (!this.settings.online) throw new TeamTalkError("login failed: the server is offline.");
    if (this.users.size >= this.settings.maxUsers) {
      throw new TeamTalkError(
        `login failed: the server is full (${this.settings.maxUsers} users).`,
      );
    }
    // Server-assigned IDs are fresh on every login — the whole reason the
    // suite tracks people by username instead of by ID.
    const id = this.nextUserId++;
    const user: SimUser = {
      id,
      username,
      nickname: nickname || username || `user ${id}`,
      channelId: CHANNEL_ROOT,
      sessionId,
    };
    this.users.set(id, user);
    return user;
  }

  logout(userId: number): void {
    const user = this.users.get(userId);
    if (!user) return;
    this.users.delete(userId);
    this.broadcast(user.channelId, {
      kind: "user_left",
      user,
    });
  }

  join(userId: number, channelId: number, password: string): void {
    const user = this.users.get(userId);
    if (!user) throw new TeamTalkError("join channel failed: not logged in.");
    const channel = this.channels.find((candidate) => candidate.id === channelId);
    if (!channel) throw new TeamTalkError("join channel failed: unknown channel ID.");
    if (channel.password && channel.password !== password) {
      throw new TeamTalkError("join channel failed: invalid channel password.");
    }
    user.channelId = channelId;
    this.broadcast(channelId, { kind: "user_joined", user });
  }

  leave(userId: number): void {
    const user = this.users.get(userId);
    if (!user) throw new TeamTalkError("leave channel failed: not logged in.");
    user.channelId = CHANNEL_ROOT;
  }

  sendChannel(userId: number, text: string): number {
    const sender = this.users.get(userId);
    if (!sender) throw new TeamTalkError("send channel message failed: not logged in.");
    // TeamTalk relays a channel message to the other users in the channel and
    // never back to the sender.
    let recipients = 0;
    for (const user of this.users.values()) {
      if (user.channelId !== sender.channelId) continue;
      if (user.id === sender.id) continue;
      recipients += 1;
      this.deliver(user.sessionId, {
        kind: "text",
        event: {
          type: 2,
          fromUserId: sender.id,
          fromUsername: sender.username,
          fromNickname: sender.nickname,
          toUserId: 0,
          channelId: sender.channelId,
          text,
          more: false,
        },
      });
    }
    return recipients;
  }

  sendPrivate(userId: number, toUserId: number, text: string): void {
    const sender = this.users.get(userId);
    if (!sender) throw new TeamTalkError("send private message failed: not logged in.");
    const target = this.users.get(toUserId);
    if (!target) throw new TeamTalkError("send private message failed: user is not online.");
    this.deliver(target.sessionId, {
      kind: "text",
      event: {
        type: 1,
        fromUserId: sender.id,
        fromUsername: sender.username,
        fromNickname: sender.nickname,
        toUserId: target.id,
        channelId: target.channelId,
        text,
        more: false,
      },
    });
  }

  private broadcast(channelId: number, event: SimEvent): void {
    for (const user of this.users.values()) {
      if (user.channelId !== channelId) continue;
      this.deliver(user.sessionId, event);
    }
  }

  // ----- views ------------------------------------------------------------ //

  channelPath(channel: SimChannel): string {
    const parts: string[] = [];
    let current: SimChannel | undefined = channel;
    const guard = new Set<number>();
    while (current && !guard.has(current.id)) {
      guard.add(current.id);
      if (current.parentId === 0) {
        parts.unshift("");
        break;
      }
      parts.unshift(current.name);
      current = this.channels.find((candidate) => candidate.id === current?.parentId);
    }
    return parts.length > 1 ? parts.join("/") : "/";
  }

  listChannels(): ChannelInfo[] {
    return this.channels
      .map((channel) => ({
        id: channel.id,
        parentId: channel.parentId,
        name: channel.name,
        path: this.channelPath(channel),
        passwordRequired: channel.password.length > 0,
        hidden: channel.hidden,
      }))
      .sort((a, b) => a.path.localeCompare(b.path) || a.id - b.id);
  }

  listUsers(): UserInfo[] {
    const result: UserInfo[] = [];
    for (const user of this.users.values()) {
      const channel = this.channels.find((candidate) => candidate.id === user.channelId);
      result.push({
        id: user.id,
        username: user.username,
        nickname: user.nickname,
        channelId: user.channelId,
        channelPath: channel ? this.channelPath(channel) : "/",
      });
    }
    return result.sort(
      (a, b) => (a.nickname || a.username).localeCompare(b.nickname || b.username) || a.id - b.id,
    );
  }

  // ----- churn ------------------------------------------------------------ //

  /** Adds one synthetic user, for testing continuous new-joiner mode. */
  spawnUser(): SimUser | null {
    if (this.users.size >= this.settings.maxUsers) return null;
    const username = AUTO_NAMES[Math.floor(Math.random() * AUTO_NAMES.length)] ?? "guest";
    const suffix = Math.floor(Math.random() * 90 + 10);
    const id = this.nextUserId++;
    const lobby = this.channels.find((channel) => channel.name === "Lobby") ?? this.channels[0];
    const user: SimUser = {
      id,
      username: `${username}${suffix}`,
      nickname: username.charAt(0).toUpperCase() + username.slice(1) + suffix,
      channelId: lobby ? lobby.id : CHANNEL_ROOT,
      sessionId: `auto-${id}`,
    };
    this.users.set(id, user);
    return user;
  }

  removeOneSynthetic(): void {
    for (const [id, user] of Array.from(this.users.entries())) {
      if (user.sessionId.startsWith("auto-")) {
        this.users.delete(id);
        return;
      }
    }
  }

  /** Auto-join/leave churn so continuous modes have something to discover. */
  syncAutoChurn(): void {
    if (this.settings.autoJoin && this.autoTimer === null) {
      this.autoTimer = window.setInterval(() => {
        if (Math.random() < 0.5) this.spawnUser();
        else this.removeOneSynthetic();
      }, 2500);
      return;
    }
    if (!this.settings.autoJoin && this.autoTimer !== null) {
      window.clearInterval(this.autoTimer);
      this.autoTimer = null;
    }
  }

  /** Short settle used between simulated operations. */
  async settle(ms = 5): Promise<void> {
    await sleep(scaled(ms));
  }
}
