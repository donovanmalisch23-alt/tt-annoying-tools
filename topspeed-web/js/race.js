// A race session: single race against computer players or a time trial. Ported from the original
// Level.cpp / LevelSingleRace.cpp / LevelTimeTrial.cpp, with the touch layout of the remake's
// mobile build (Game/Drive/Touch): the screen is split into a top-left info zone, a top-right info
// zone and a bottom vehicle zone.

import { Track } from './track.js';
import { PlayerCar, Bot, State } from './car.js';
import { VEHICLES } from './data-vehicles.js';
import { G } from './gestures.js';
import { getRecord, setRecord } from './settings.js';

const COPILOT_COUNTS = {
    easyleft: 5, left: 4, hardleft: 5, hairpinleft: 4,
    easyright: 4, right: 4, hardright: 3, hairpinright: 4,
    asphalt: 3, gravel: 3, water: 3, sand: 3, snow: 3
};
const TONE_FOR_TYPE = {
    EasyLeft: 'easy', Left: 'normal', HardLeft: 'hard', HairpinLeft: 'hairpin',
    EasyRight: 'easy', Right: 'normal', HardRight: 'hard', HairpinRight: 'hairpin'
};
const MAX_BOTS = 9;

export const ZONES = {
    infoLeft: 'drive_info_left',
    infoRight: 'drive_info_right',
    vehicle: 'drive_vehicle'
};
const SPLIT_Y = 0.5;
const SPLIT_X = 0.5;
const AXIS_TRAVEL = 0.22;

const rnd = n => Math.floor(Math.random() * n);
const pick = arr => arr[rnd(arr.length)];

export class Race {
    constructor(app, { mode, trackKey, trackName, trackData, vehicleIndex, manual }) {
        this.app = app;
        this.audio = app.audio;
        this.settings = app.settings;
        this.mode = mode; // 'single' | 'time'
        this.trackKey = trackKey;
        this.trackName = trackName;
        this.track = new Track(trackKey, trackData, this.audio);
        this.vehicle = VEHICLES[vehicleIndex];
        this.laps = this.track.isAdventure ? 1 : this.settings.laps;
        this.time = 0;          // simulation time (paused time excluded)
        this.events = [];
        this.started = false;
        this.finishedAll = false;
        this.paused = false;
        this.lap = 0;
        this.speakTime = 0;
        this.unkeyQueue = 0;
        this.lastComment = 0;
        this.acceptInfo = true;
        this.positionFinish = 0;
        this.focusedPlayer = 0;
        this.curbCooldown = 0;
        this.keys = new Set();
        this.keyEdges = new Set();
        this.gestureFlags = new Set();
        this.player = new PlayerCar(this, this.vehicle, manual);
        this.bots = [];
        const nBots = mode === 'single' ? Math.min(MAX_BOTS, this.settings.computerPlayers) : 0;
        this.playerNumber = mode === 'single' ? rnd(nBots + 1) : 0;
        for (let i = 0; i < nBots; i++) {
            const number = i >= this.playerNumber ? i + 1 : i;
            this.bots.push(new Bot(this, VEHICLES[rnd(VEHICLES.length)], number, this.settings.difficulty));
        }
        this.position = this.playerNumber + 1;
        this.positionComment = this.position;
        this.motion = { steering: 0, neutral: null };
    }

    // ---- setup -------------------------------------------------------------------------
    voicePaths() {
        const paths = ['En/Race/start321.ogg', 'En/Race/youare.ogg', 'En/Race/player.ogg', 'En/Race/pause.ogg', 'En/Race/unpause.ogg', 'En/Music/theme4.ogg'];
        for (const [name, n] of Object.entries(COPILOT_COUNTS))
            for (let i = 1; i <= n; i++) paths.push(`En/Race/Copilot/${name}${i}.ogg`);
        for (const t of ['easy', 'normal', 'hard', 'hairpin']) paths.push(`racecues/turns/${t}.ogg`);
        for (let i = 1; i <= 4; i++) paths.push(`En/Race/Info/finish${i}.ogg`);
        for (let i = 1; i <= 8; i++) paths.push(`En/Race/Info/front${i}.ogg`, `En/Race/Info/tail${i}.ogg`);
        for (let i = 1; i <= 10; i++) paths.push(`En/Race/Info/player${i}.ogg`);
        for (let i = 1; i <= 9; i++) paths.push(`En/Race/Info/youarepos${i}.ogg`, `En/Race/Info/finished${i}.ogg`);
        paths.push('En/Race/Info/youareposlast.ogg', 'En/Race/Info/finishedlast.ogg');
        for (let i = 1; i < this.laps; i++) paths.push(`En/Race/Info/laps2go${i}.ogg`);
        for (let i = 1; i <= 10; i++) paths.push(`En/Numbers/${i}.ogg`);
        for (let i = 1; i <= 12; i++) paths.push(`Legacy/unkey${i}.wav`);
        return paths;
    }

    async load(onProgress) {
        const paths = [
            ...this.track.soundPaths(), ...this.player.soundPaths(),
            ...this.bots.flatMap(b => b.soundPaths()), ...this.voicePaths()
        ].filter(Boolean);
        const unique = [...new Set(paths)];
        let done = 0;
        await Promise.all(unique.map(p => this.audio.load(p).then(() => onProgress && onProgress(++done / unique.length))));
        this.track.createSounds();
        this.player.createSounds();
        for (const b of this.bots) b.createSounds();
        await Promise.all([...this.player.allSounds(), ...this.bots.flatMap(b => b.sounds)].map(s => s.ready));

        // Starting grid, as in LevelSingleRace::initialize.
        const grid = n => ({ x: n % 2 ? 3000 : -3000, y: 14000 - n * 2000 });
        const g = this.mode === 'single' ? grid(this.playerNumber) : { x: 0, y: 0 };
        this.player.x = g.x;
        this.player.y = g.y;
        for (const b of this.bots) {
            const p = grid(b.playerNumber);
            b.x = p.x;
            b.y = p.y;
        }
    }

    begin() {
        this.track.startAmbience();
        if (this.mode === 'single') {
            this.speak('En/Race/youare.ogg');
            this.speak('En/Race/player.ogg');
            this.speak(`En/Numbers/${this.playerNumber + 1}.ogg`);
            for (const b of this.bots) b.pendingStart();
        } else {
            const rec = getRecord(this.trackKey);
            this.app.speech.say(`${this.trackName}. ${rec ? 'Track record ' + formatTime(rec, true) : 'No track record yet'}.`);
        }
        this.at(3.0, () => this.player.start());
        this.at(1.5, () => this.play('En/Race/start321.ogg'));
        this.at(6.5, () => {
            this.started = true;
            this.raceStartTime = this.time;
            this.lap = 0;
        });
    }

    // ---- scheduling & speech -----------------------------------------------------------
    at(delay, fn) {
        this.events.push({ time: this.time + delay, fn });
    }

    play(path, opts) {
        return this.audio.playOnce(path, 'voice', opts);
    }

    // Queue a voice clip after whatever is already being said (Level::speak), optionally
    // followed by a radio "unkey" click.
    speak(path, unkey = false) {
        const data = this.audio.peek(path);
        const len = data ? data.buffer.duration : 1.0;
        const delay = Math.max(0, this.speakTime - this.time);
        if (delay > 0) this.at(delay, () => this.play(path));
        else this.play(path);
        this.speakTime = this.time + delay + len;
        if (unkey) {
            this.unkeyQueue++;
            this.at(delay + len, () => {
                this.unkeyQueue--;
                if (this.unkeyQueue === 0) this.speak(`Legacy/unkey${rnd(12) + 1}.wav`);
            });
        }
    }

    // ---- input -------------------------------------------------------------------------
    setZones(gestures) {
        gestures.setZones([
            { id: ZONES.infoLeft, x: 0, y: 0, w: SPLIT_X, h: SPLIT_Y },
            { id: ZONES.infoRight, x: SPLIT_X, y: 0, w: 1 - SPLIT_X, h: SPLIT_Y },
            { id: ZONES.vehicle, x: 0, y: SPLIT_Y, w: 1, h: 1 - SPLIT_Y }
        ]);
    }

    gesture(intent, zone) {
        this.gestureFlags.add(`${zone}:${intent}`);
    }

    keyDown(e) {
        if (!this.keys.has(e.code)) this.keyEdges.add(e.code);
        this.keys.add(e.code);
    }

    keyUp(e) {
        this.keys.delete(e.code);
    }

    _was(zone, intent) {
        return this.gestureFlags.has(`${zone}:${intent}`);
    }

    _edge(...codes) {
        return codes.some(c => this.keyEdges.has(c));
    }

    readInput() {
        const k = c => this.keys.has(c);
        const gi = this.app.gestures;
        let steering = 0, throttle = 0, brake = 0, clutch = 0, horn = false;

        // Keyboard (desktop testing and Bluetooth keyboards): the original default keys.
        if (k('ArrowLeft')) steering -= 100;
        if (k('ArrowRight')) steering += 100;
        if (k('ArrowUp')) throttle = 100;
        if (k('ArrowDown')) brake = -100;
        if (k('Space')) horn = true;
        if (k('ShiftLeft') || k('ShiftRight')) clutch = 100;

        // Motion steering (tilt the phone like a wheel).
        if (this.settings.motionSteering) steering = Math.max(-100, Math.min(100, steering + this.motion.steering));

        // Bottom zone: one finger drags from where it touched down.
        const v = gi.zoneState(ZONES.vehicle);
        if (v.active && v.fingerCount === 1) {
            const dx = v.x - v.startX;
            const dy = v.y - v.startY;
            const scale = d => Math.min(100, Math.round(Math.abs(d) / AXIS_TRAVEL * 100));
            if (this.settings.touchLayout === 0) {
                // Original layout: drag right = throttle, left = brake; drag up/down = steer.
                if (dx > 0) throttle = Math.max(throttle, scale(dx));
                else if (dx < 0) brake = Math.min(brake, -scale(dx));
                if (!this.settings.motionSteering) {
                    if (dy < 0) steering = -scale(dy);
                    else if (dy > 0) steering = scale(dy);
                }
            } else {
                // Alternative layout: drag up = throttle, down = brake; drag left/right = steer.
                if (dy < 0) throttle = Math.max(throttle, scale(dy));
                else if (dy > 0) brake = Math.min(brake, -scale(dy));
                if (!this.settings.motionSteering) {
                    if (dx < 0) steering = -scale(dx);
                    else if (dx > 0) steering = scale(dx);
                }
            }
        }
        // Top zones: holding one finger = clutch (left) / horn (right).
        const l = gi.zoneState(ZONES.infoLeft);
        if (l.active && l.fingerCount === 1 && l.duration > 150) clutch = 100;
        const r = gi.zoneState(ZONES.infoRight);
        if (r.active && r.fingerCount === 1 && r.duration > 150) horn = true;

        return {
            steering: Math.max(-100, Math.min(100, steering)), throttle, brake, clutch, horn,
            gearUp: this._was(ZONES.vehicle, G.TwoFingerSwipeUp) || this._edge('KeyA'),
            gearDown: this._was(ZONES.vehicle, G.TwoFingerSwipeDown) || this._edge('KeyZ')
        };
    }

    handleCommands() {
        const W = (zone, g) => this._was(zone, g);
        const L = ZONES.infoLeft, R = ZONES.infoRight, V = ZONES.vehicle;
        if (W(L, G.ThreeFingerTripleTap) || this._edge('KeyP')) return this.togglePause();
        if (W(L, G.SwipeDown) || this._edge('Escape')) return this.app.openRaceMenu();
        if (this.paused) return;
        if (W(V, G.DoubleTap) || this._edge('Enter')) this.restartEngine();
        if (W(L, G.DoubleTap) || this._edge('KeyS')) this.reportSpeed();
        if (W(R, G.TwoFingerDoubleTap) || this._edge('KeyD')) this.reportDistance();
        if (W(R, G.ThreeFingerTap) || this._edge('KeyG')) this.reportGear();
        if (W(R, G.DoubleTap) || this._edge('KeyL')) this.reportLap();
        if (W(L, G.TwoFingerDoubleTap) || this._edge('KeyR')) this.reportRacePercent();
        if (W(L, G.ThreeFingerDoubleTap) || this._edge('KeyE')) this.reportLapPercent();
        if (W(L, G.TwoFingerTripleTap) || this._edge('KeyT')) this.reportTime();
        if (W(R, G.ThreeFingerDoubleTap) || this._edge('KeyI')) this.requestInfo();
        if (W(R, G.TwoFingerSwipeUp) || this._edge('BracketRight')) this.playerInfo(+1);
        if (W(R, G.TwoFingerSwipeDown) || this._edge('BracketLeft')) this.playerInfo(-1);
        if (W(R, G.TwoFingerTripleTap) || this._edge('Backslash')) this.playerInfo(0);
        for (let i = 1; i <= 9; i++) if (this._edge(`Digit${i}`)) this.playerInfo(null, i - 1);
    }

    // ---- frame -------------------------------------------------------------------------
    update(dt) {
        dt = Math.min(dt, 0.1);
        this.handleCommands();
        if (!this.paused) this.step(dt);
        this.gestureFlags.clear();
        this.keyEdges.clear();
    }

    step(dt) {
        this.time += dt;
        const due = this.events.filter(e => e.time <= this.time);
        this.events = this.events.filter(e => e.time > this.time);
        for (const e of due) e.fn();

        const input = this.readInput();
        this.updatePositions();
        this.player.run(dt, input);
        this.track.run(this.player.y);

        for (const b of this.bots) {
            b.run(dt, this.player.x, this.player.y);
            if (!b.finished && this.track.lap(b.y) > this.laps) {
                b.finished = true;
                b.stop();
                this.speak(`En/Race/Info/player${b.playerNumber + 1}.ogg`, true);
                this.speak(this.finishedSound(this.positionFinish++), true);
                this.checkFinish();
            }
        }

        const road = this.track.road(this.player.y);
        this.player.evaluate(road);
        const next = this.track.nextRoad(this.player.y, this.player.speed, this.settings.curveAnnouncement);
        if (next) this.callNextRoad(next);

        const lapNow = this.track.lap(this.player.y);
        if (this.started && lapNow > this.lap && !this.player.finished) {
            this.lap = lapNow;
            if (this.lap > this.laps) this.playerFinished();
            else if (this.settings.automaticInfo > 0 && this.lap > 1) {
                this.speak(`En/Race/Info/laps2go${this.laps - this.lap + 1}.ogg`, true);
            }
        }

        this.checkBumps();
        if (this.mode === 'single') {
            this.lastComment += dt;
            if (this.settings.automaticInfo > 1 && this.lastComment > 6) {
                this.comment(true);
                this.lastComment = 0;
            }
        }
        this.curbCooldown = Math.max(0, this.curbCooldown - dt);
        this.app.updateHud(this);
    }

    updatePositions() {
        let pos = 1;
        for (const b of this.bots) if (b.y > this.player.y) pos++;
        this.position = pos;
    }

    callNextRoad(next) {
        const s = this.settings;
        if (s.copilot > 0 && next.type !== 'Straight') {
            if (s.curveCues === 1) {
                const pan = next.type.includes('Left') ? -70 : 70;
                this.play(`racecues/turns/${TONE_FOR_TYPE[next.type]}.ogg`, { pan });
            } else {
                const name = next.type.toLowerCase();
                this.play(`En/Race/Copilot/${name}${rnd(COPILOT_COUNTS[name]) + 1}.ogg`);
            }
        }
        const current = this.currentSurfaceCalled || this.track.segments[0].surface;
        if (s.copilot > 1 && next.surface !== current) {
            const name = next.surface.toLowerCase();
            this.at(1.0, () => this.play(`En/Race/Copilot/${name}${rnd(COPILOT_COUNTS[name]) + 1}.ogg`));
        }
        this.currentSurfaceCalled = next.surface;
    }

    checkBumps() {
        if (this.player.state !== State.Running) return;
        for (const b of this.bots) {
            if (b.finished) continue;
            if (Math.abs(this.player.x - b.x) < 1000 && Math.abs(this.player.y - b.y) < 500) {
                const bumpX = this.player.x - b.x;
                const bumpY = this.player.y - b.y;
                const bumpSpeed = this.player.speed - b.speed;
                this.player.bump(bumpX, bumpY, bumpSpeed);
                b.bump(-bumpX, -bumpY, -bumpSpeed);
            }
        }
    }

    finishedSound(index) {
        return index >= this.bots.length ? 'En/Race/Info/finishedlast.ogg' : `En/Race/Info/finished${index + 1}.ogg`;
    }

    playerFinished() {
        this.player.finished = true;
        this.raceTime = (this.time - this.raceStartTime) * 1000;
        this.speak(`En/Race/Info/finish${rnd(4) + 1}.ogg`, true);
        this.player.manual = false;
        this.player.quiet();
        this.player.stop();
        if (this.mode === 'single') {
            this.finishPosition = this.positionFinish + 1;
            this.speak(`En/Race/Info/player${this.playerNumber + 1}.ogg`, true);
            this.speak(this.finishedSound(this.positionFinish++), true);
        }
        this.checkFinish();
    }

    checkFinish() {
        if (!this.player.finished || this.finishedAll) return;
        if (this.bots.some(b => !b.finished)) return;
        this.finishedAll = true;
        this.at(1.0 + Math.max(0, this.speakTime - this.time), () => this.announceResult());
    }

    announceResult() {
        const parts = [];
        const lines = [];
        if (this.mode === 'single') {
            parts.push(`You finished ${ordinal(this.finishPosition)} of ${this.bots.length + 1}.`);
            lines.push(`Position: ${ordinal(this.finishPosition)} of ${this.bots.length + 1}`);
        }
        parts.push(`Your time: ${formatTime(this.raceTime, true)}.`);
        lines.push(`Time: ${formatTime(this.raceTime, true)}`);
        if (this.mode === 'time') {
            const rec = getRecord(this.trackKey);
            if (!rec || this.raceTime < rec) {
                setRecord(this.trackKey, Math.round(this.raceTime));
                parts.push('New track record!');
                lines.push('New track record');
            } else {
                parts.push(`Track record: ${formatTime(rec, true)}.`);
                lines.push(`Track record: ${formatTime(rec, true)}`);
            }
        }
        this.app.speech.say(parts.join(' '));
        this.app.finishRace(this, lines);
    }

    // Spoken race commentary (LevelSingleRace::comment).
    comment(automatic) {
        if (!this.started || this.lap > this.laps || this.mode !== 'single') return;
        let position = 1, inFront = null, inFrontDist = 50000, onTail = null, onTailDist = 50000;
        for (const b of this.bots) {
            if (b.y > this.player.y) {
                position++;
                const d = b.y - this.player.y;
                if (d < inFrontDist) { inFront = b; inFrontDist = d; }
            } else if (b.y < this.player.y) {
                const d = this.player.y - b.y;
                if (d < onTailDist) { onTail = b; onTailDist = d; }
            }
        }
        const posSound = p => p === this.bots.length + 1 ? 'En/Race/Info/youareposlast.ogg' : `En/Race/Info/youarepos${p}.ogg`;
        if (automatic && position !== this.positionComment) {
            this.speak(posSound(position), true);
            this.positionComment = position;
            return;
        }
        if (inFrontDist < onTailDist) {
            if (inFront) {
                this.speak(`En/Race/Info/player${inFront.playerNumber + 1}.ogg`, true);
                this.speak(`En/Race/Info/front${rnd(8) + 1}.ogg`, true);
                return;
            }
        } else if (onTail) {
            this.speak(`En/Race/Info/player${onTail.playerNumber + 1}.ogg`, true);
            this.speak(`En/Race/Info/tail${rnd(8) + 1}.ogg`, true);
            return;
        }
        if (!inFront && !onTail && !automatic) {
            this.speak(posSound(position), true);
            this.positionComment = position;
        }
    }

    // ---- reports (spoken with the browser's voice) ---------------------------------------
    say(text) { this.app.speech.say(text); }

    speedText(units) {
        const kmh = units / 100;
        return this.settings.units === 1 ? `${Math.round(kmh * 0.621371)} miles per hour` : `${Math.round(kmh)} kilometers per hour`;
    }

    distanceText(units) {
        const m = units / 100;
        if (this.settings.units === 1) {
            const ft = m * 3.28084;
            return ft >= 5280 ? `${(ft / 5280).toFixed(2)} miles` : `${Math.round(ft)} feet`;
        }
        return m >= 1000 ? `${(m / 1000).toFixed(2)} kilometers` : `${Math.round(m)} meters`;
    }

    reportSpeed() { this.say(this.speedText(this.player.speed)); }

    reportDistance() {
        const total = this.track.lapDistance * this.laps;
        const done = Math.max(0, Math.min(total, this.player.y));
        this.say(`${this.distanceText(done)} driven, ${this.distanceText(total - done)} to go`);
    }

    reportGear() {
        const g = this.player.gear;
        this.say(`Gear ${g}${this.player.manual ? '' : ', automatic'}`);
    }

    announceGear(g) {
        this.say(`${g}`);
    }

    reportLap() {
        if (!this.started) return this.say('Race not started');
        const lap = Math.min(this.laps, Math.max(1, this.lap));
        const t = this.track.turnInfo(this.player.y);
        let text = `Lap ${lap} of ${this.laps}`;
        if (t && !t.lapEnd) text += t.inTurn ? `, in turn ${t.turn}` : `, approaching turn ${t.turn}`;
        else if (t && t.lapEnd) text += ', completing the lap';
        this.say(text);
    }

    reportRacePercent() {
        const p = Math.max(0, Math.min(100, this.player.y / (this.track.lapDistance * this.laps) * 100));
        this.say(`${p.toFixed(1)} percent of the race`);
    }

    reportLapPercent() {
        const lapStart = this.track.lapDistance * (Math.max(1, this.lap) - 1);
        const p = Math.max(0, Math.min(100, (this.player.y - lapStart) / this.track.lapDistance * 100));
        this.say(`${Math.floor(p)} percent of the lap`);
    }

    reportTime() {
        if (!this.started) return this.say('Race not started');
        const ms = this.player.finished ? this.raceTime : (this.time - this.raceStartTime) * 1000;
        this.say(formatTime(ms, false));
    }

    requestInfo() {
        if (this.mode !== 'single') return this.reportLap();
        if (this.time - (this._lastRequest || -10) < 2) return;
        this._lastRequest = this.time;
        this.comment(false);
        this.lastComment = 0;
    }

    // Player info: cycle through the field (+1 / -1), repeat (0) or pick by grid number.
    playerInfo(delta, number) {
        const total = this.bots.length + 1;
        if (number !== undefined) {
            if (number >= total) return;
            this.focusedPlayer = number;
        } else if (delta) {
            this.focusedPlayer = (this.focusedPlayer + delta + total) % total;
        }
        const n = this.focusedPlayer;
        if (n === this.playerNumber) {
            return this.say(`Player ${n + 1}, you, ${this.vehicle.name}, position ${this.position}`);
        }
        const b = this.bots.find(x => x.playerNumber === n);
        if (!b) return;
        const gap = b.y - this.player.y;
        let pos = 1;
        for (const o of this.bots) if (o !== b && o.y > b.y) pos++;
        if (this.player.y > b.y) pos++;
        const where = gap >= 0 ? `${this.distanceText(gap)} ahead` : `${this.distanceText(-gap)} behind`;
        this.say(`Player ${n + 1}, ${b.v.name}, position ${pos}, ${b.finished ? 'finished' : where}`);
    }

    restartEngine() {
        if (this.player.state === State.Stopped && this.started) this.player.start();
    }

    curbWarning() {
        if (this.curbCooldown > 0) return;
        this.curbCooldown = 0.35;
        this.vibrate(25);
    }

    onPlayerCrash() { /* hook for HUD */ }

    vibrate(pattern) {
        if (this.settings.vibration && navigator.vibrate) {
            try { navigator.vibrate(pattern); } catch (_) { /* ignored */ }
        }
    }

    // ---- pause -------------------------------------------------------------------------
    togglePause() {
        if (this.paused) this.resume();
        else this.pause();
    }

    pause() {
        if (this.paused || this.finishedAll) return;
        this.paused = true;
        this.app.muteRaceBuses(true);
        this.audio.playOnce('En/Race/pause.ogg', 'menu');
        this.pauseMusic = this.audio.create('En/Music/theme4.ogg', 'music');
        this.pauseMusic.volume(80);
        this.pauseMusic.play(true);
        this.app.speech.say('Paused. Triple tap with three fingers in the top left to continue.');
    }

    resume() {
        if (!this.paused) return;
        this.paused = false;
        if (this.pauseMusic) this.pauseMusic.dispose();
        this.pauseMusic = null;
        this.app.muteRaceBuses(false);
        this.audio.playOnce('En/Race/unpause.ogg', 'menu');
    }

    dispose() {
        if (this.pauseMusic) this.pauseMusic.dispose();
        this.player.dispose();
        for (const b of this.bots) b.dispose();
        this.track.dispose();
    }
}

export function formatTime(ms, detailed) {
    const total = Math.max(0, Math.round(ms));
    const minutes = Math.floor(total / 60000);
    const seconds = Math.floor((total % 60000) / 1000);
    const millis = total % 1000;
    const parts = [];
    if (minutes) parts.push(`${minutes} ${minutes === 1 ? 'minute' : 'minutes'}`);
    if (detailed) parts.push(`${seconds}.${String(millis).padStart(3, '0')} seconds`);
    else parts.push(`${seconds} ${seconds === 1 ? 'second' : 'seconds'}`);
    return parts.join(' ');
}

function ordinal(n) {
    const s = ['th', 'st', 'nd', 'rd'];
    const v = n % 100;
    return n + (s[(v - 20) % 10] || s[v] || s[0]);
}
