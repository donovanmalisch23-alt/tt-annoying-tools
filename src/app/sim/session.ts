import type { ChannelInfo, ConnectionConfig, TextEvent, UserInfo } from "../core/types";
import { TeamTalkError } from "../core/types";
import { logbus } from "../core/logbus";
import { advanceReal, sleep } from "./clock";
import type { SimEvent, SimPeer, SimServer } from "./server";

export interface SessionOptions {
  /** Short name used as a log prefix, e.g. "bot-3" or "main". */
  label: string;
  /** Bots stay quiet about routine reconnects so the console stays readable. */
  quiet?: boolean;
  /** Login joins the configured channel. The login-cycle tool turns this off. */
  joinOnLogin?: boolean;
}

let sessionSeq = 0;

/**
 * Web/PWA counterpart of `tt_teamtalk.TeamTalkSession`: one connection to the
 * simulated server, a single internal event queue (the sole consumer of
 * server events, exactly like the CLI's one event-pump thread), command
 * timeout handling and kick resistance.
 */
export class SimSession implements SimPeer {
  readonly id: string;

  private readonly server: SimServer;
  private readonly config: ConnectionConfig;
  private readonly label: string;
  private readonly quiet: boolean;
  private readonly joinOnLogin: boolean;

  private userId: number | null = null;
  private nickname = "";
  private channelId: number | null = null;
  private connected = false;
  private loggedIn = false;
  private closed = false;
  private lastLoss: string | null = null;

  private textQueue: TextEvent[] = [];
  private textWaiters: Array<(event: TextEvent | null) => void> = [];

  /** How many times this session had to recover after being kicked. */
  recoveries = 0;
  /** Exactly how many messages this session has actually delivered. */
  messagesSent = 0;

  constructor(server: SimServer, config: ConnectionConfig, options: SessionOptions) {
    this.id = `sess-${++sessionSeq}`;
    this.server = server;
    this.config = config;
    this.label = options.label;
    this.quiet = options.quiet ?? false;
    this.joinOnLogin = options.joinOnLogin ?? true;
    server.registerPeer(this);
  }

  // ----- event pump ------------------------------------------------------- //

  receive(event: SimEvent): void {
    if (this.closed) return;
    switch (event.kind) {
      case "text": {
        const waiter = this.textWaiters.shift();
        if (waiter) waiter(event.event);
        else {
          this.textQueue.push(event.event);
          if (this.textQueue.length > 200) this.textQueue.shift();
        }
        break;
      }
      case "con_lost": {
        if (!this.connected) return;
        this.connected = false;
        this.loggedIn = false;
        this.userId = null;
        this.lastLoss = event.reason;
        if (!this.quiet) logbus.warn(`[${this.label}] connection lost: ${event.reason}`);
        break;
      }
      default:
        break;
    }
  }

  get isConnected(): boolean {
    return this.connected && !this.closed;
  }

  get labelText(): string {
    return this.label;
  }

  /** Resolves with the next text event, or null once the timeout elapses. */
  async nextText(timeoutMs: number): Promise<TextEvent | null> {
    const queued = this.textQueue.shift();
    if (queued) return queued;
    return new Promise<TextEvent | null>((resolve) => {
      const started = Date.now();
      const waiter = (event: TextEvent | null) => {
        this.textWaiters = this.textWaiters.filter((candidate) => candidate !== waiter);
        // Advance the simulated clock by however long the wait actually took,
        // whether an event arrived or the timeout ran out.
        advanceReal(Date.now() - started);
        resolve(event);
      };
      this.textWaiters.push(waiter);
      void sleep(timeoutMs).then(() => waiter(null));
    });
  }

  drainText(): TextEvent[] {
    const drained = this.textQueue;
    this.textQueue = [];
    return drained;
  }

  // ----- connection ------------------------------------------------------- //

  async connect(): Promise<void> {
    if (this.closed) throw new TeamTalkError("session is closed");
    if (this.connected) return;
    await sleep(this.server.settings.latencyMs);
    if (this.server.saturated && !this.quiet) {
      logbus.warn(`[${this.label}] connecting while the server is saturated`);
    }
    this.connected = true;
    this.lastLoss = null;
    if (!this.quiet) {
      logbus.sys(`[${this.label}] connected to ${this.config.host}:${this.config.tcpPort}`);
    }
  }

  async disconnect(): Promise<void> {
    if (this.closed) return;
    if (this.loggedIn && this.userId !== null) {
      try {
        this.server.logout(this.userId);
      } catch {
        /* server already dropped us */
      }
    }
    this.loggedIn = false;
    this.userId = null;
    this.channelId = null;
    this.connected = false;
    this.closed = true;
    this.server.unregisterPeer(this.id);
    if (!this.quiet) logbus.sys(`[${this.label}] disconnected`);
  }

  // ----- low-level command path ------------------------------------------- //

  /**
   * Latency + loss simulation plus flood-protection accounting, then a check
   * that the connection survived. Any operation whose command is lost under
   * load raises, which is what makes the tools' retry paths real.
   */
  private async command(action: string): Promise<void> {
    if (this.closed) throw new TeamTalkError(`${action} failed: session is closed.`);
    await this.ensure();
    await this.server.command(action);
    this.server.noteCommand(this.id);
    if (!this.connected) {
      throw new TeamTalkError(`${action} failed: connection lost (${this.lastLoss ?? "unknown"}).`);
    }
  }

  /**
   * Kick resistance, matching the CLI: on a lost connection the session waits
   * the configured reconnect delay, reconnects, logs back in and rejoins the
   * channel it was in.
   */
  private async ensure(): Promise<void> {
    if (this.connected) return;
    if (!this.config.kickResistance) {
      throw new TeamTalkError("not connected to the server");
    }
    this.recoveries += 1;
    if (!this.quiet) {
      logbus.warn(
        `[${this.label}] recovering (attempt ${this.recoveries}): ` +
          `reconnect in ${this.config.reconnectDelaySec}s, then re-login and rejoin`,
      );
    }
    const wantedChannel = this.channelId;
    await sleep(this.config.reconnectDelaySec * 1000);
    await this.connect();
    await this.loginRaw();
    if (this.joinOnLogin && wantedChannel !== null) await this.joinRaw(wantedChannel, null, "");
    if (!this.quiet) logbus.ok(`[${this.label}] recovered after ${this.recoveries} attempt(s)`);
  }

  private async loginRaw(): Promise<void> {
    const user = this.server.login(this.id, this.config.nickname, this.config.username);
    this.userId = user.id;
    this.nickname = user.nickname;
    this.loggedIn = true;
    if (this.joinOnLogin && (this.config.channelId !== null || this.config.channelPath)) {
      const target =
        this.config.channelId ??
        this.server.listChannels().find((channel) => channel.path === this.config.channelPath)?.id ??
        null;
      if (target !== null) await this.joinRaw(target, null, this.config.channelPassword);
    }
  }

  private async joinRaw(
    channelId: number,
    _path: string | null,
    password: string,
  ): Promise<void> {
    this.server.join(this.userId as number, channelId, password);
    this.channelId = channelId;
  }

  // ----- public session API ---------------------------------------------- //

  async login(): Promise<void> {
    await this.command("login");
    await this.loginRaw();
    if (!this.quiet) {
      logbus.ok(`[${this.label}] logged in as ${this.config.username || "anonymous"}`);
    }
  }

  async logout(): Promise<void> {
    if (!this.loggedIn || this.userId === null) {
      throw new TeamTalkError("logout failed: not logged in");
    }
    await this.command("logout");
    this.server.logout(this.userId);
    this.loggedIn = false;
    this.userId = null;
    this.channelId = null;
  }

  async joinByPath(path: string, password = ""): Promise<void> {
    const channel = this.server.listChannels().find((candidate) => candidate.path === path);
    if (!channel) throw new TeamTalkError(`join channel failed: no channel at '${path}'`);
    await this.command(`join channel ${path}`);
    await this.joinRaw(channel.id, path, password);
    if (!this.quiet) logbus.info(`[${this.label}] joined ${channel.path}`);
  }

  async joinById(channelId: number, password = ""): Promise<void> {
    await this.command(`join channel #${channelId}`);
    await this.joinRaw(channelId, null, password);
  }

  async leaveChannel(): Promise<void> {
    await this.command("leave channel");
    this.server.leave(this.userId as number);
    this.channelId = null;
  }

  /** Channel message. Returns how many live recipients the server relayed it to. */
  async sendChannelMessage(text: string): Promise<number> {
    await this.command("send channel message");
    if (!this.loggedIn || this.userId === null) throw new TeamTalkError("not logged in");
    const recipients = this.server.sendChannel(this.userId, text);
    this.messagesSent += 1;
    return recipients;
  }

  /** Private message, addressed by username so a re-login keeps retargeting correctly. */
  async sendPrivateMessage(username: string, text: string): Promise<void> {
    await this.command(`send private message to ${username}`);
    if (!this.loggedIn || this.userId === null) throw new TeamTalkError("not logged in");
    const target = this.server.listUsers().find((user) => user.username === username);
    if (!target) throw new TeamTalkError(`send private message failed: '${username}' is not online`);
    this.server.sendPrivate(this.userId, target.id, text);
    this.messagesSent += 1;
  }

  /**
   * Idle-bot keepalive: a no-op command that gives the session a chance to
   * notice a kick and recover, exactly like the CLI watchdog.
   */
  async watchdog(): Promise<boolean> {
    try {
      await this.command("watchdog check");
      return true;
    } catch {
      return false;
    }
  }

  // ----- discovery ------------------------------------------------------- //

  async channels(): Promise<ChannelInfo[]> {
    await this.command("list channels");
    return this.server.listChannels().filter((channel) => !channel.hidden);
  }

  async users(): Promise<UserInfo[]> {
    await this.command("list users");
    return this.server.listUsers();
  }

  get currentChannelId(): number | null {
    return this.channelId;
  }

  get currentUserId(): number | null {
    return this.userId;
  }

  get displayNickname(): string {
    return this.nickname || this.config.username;
  }
}
