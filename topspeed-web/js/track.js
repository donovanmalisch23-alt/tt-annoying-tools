// Track model and track ambience, ported from the original Track.cpp / the remake's RoadModel.
// Distances use the classic game units: 100 units = 1 metre. A curve shifts the whole road
// corridor sideways while you drive through it; you steer to stay inside it.

export const TYPES = ['Straight', 'EasyLeft', 'Left', 'HardLeft', 'HairpinLeft', 'EasyRight', 'Right', 'HardRight', 'HairpinRight'];
export const LANE_WIDTH = 15000;      // half width of a race track
export const ADV_LANE_WIDTH = 8000;   // half width of a street adventure
export const CALL_LENGTH = 3000;      // copilot calls the next segment 30 m before it starts

const DRIFT = {
    Straight: 0, EasyLeft: -1 / 2, Left: -2 / 3, HardLeft: -1, HairpinLeft: -3 / 2,
    EasyRight: 1 / 2, Right: 2 / 3, HardRight: 1, HairpinRight: 3 / 2
};

const NOISE_LOOPS = {
    Crowd: 'Legacy/crowd.wav', Ocean: 'Legacy/ocean.wav', Clock: 'Legacy/clock.wav',
    Pile: 'Legacy/pile.wav', Construction: 'Legacy/const.wav', River: 'Legacy/river.wav'
};
const NOISE_ONESHOTS = {
    Runway: 'Legacy/airplane.wav', Jet: 'Legacy/jet.wav', Thunder: 'Legacy/thunder.wav',
    Helicopter: 'Legacy/helicopter.wav', Owl: 'Legacy/owl.wav'
};
const NOISE_PAN = { Ocean: -10, Clock: 25 };
const WEATHER = { Rain: 'Legacy/rain.wav', Wind: 'Legacy/wind.wav', Storm: 'Legacy/storm.wav' };
const AMBIENCE = { Desert: 'Legacy/desert.wav', Airport: 'Legacy/airport.wav' };

export class Track {
    constructor(key, data, audio) {
        this.key = key;
        this.audio = audio;
        this.weather = data.weather;
        this.ambience = data.ambience;
        this.segments = data.segments.map(([type, surface, noise, length]) => ({
            type, surface, noise, length: Math.round(length * 100)
        }));
        this.isAdventure = key.startsWith('adv');
        this.laneWidth = this.isAdventure ? ADV_LANE_WIDTH : LANE_WIDTH;
        this.lapDistance = 0;
        let center = 0;
        this.starts = [];
        for (const s of this.segments) {
            this.starts.push(this.lapDistance);
            this.lapDistance += s.length;
            center += s.length * DRIFT[s.type];
        }
        this.lapCenter = center;
        // Player-relative state for the fixed-distance copilot.
        this.currentRoad = 0;
        this.relPos = 0;
        this.prevRelPos = 0;
        this.lastCalled = 0;
        // Noise state
        this.noisePlaying = false;
        this.noiseStart = 0;
        this.noiseEnd = 0;
        this.noiseLength = 1;
        this.sounds = {};
    }

    soundPaths() {
        const paths = [...Object.values(NOISE_LOOPS), ...Object.values(NOISE_ONESHOTS)];
        if (WEATHER[this.weather]) paths.push(WEATHER[this.weather]);
        if (AMBIENCE[this.ambience]) paths.push(AMBIENCE[this.ambience]);
        return paths;
    }

    createSounds() {
        const used = new Set(this.segments.map(s => s.noise));
        for (const [noise, path] of Object.entries({ ...NOISE_LOOPS, ...NOISE_ONESHOTS }))
            if (used.has(noise)) this.sounds[noise] = this.audio.create(path, 'ambience');
        if (WEATHER[this.weather]) this.weatherSound = this.audio.create(WEATHER[this.weather], 'ambience');
        if (AMBIENCE[this.ambience]) this.ambienceSound = this.audio.create(AMBIENCE[this.ambience], 'ambience');
    }

    startAmbience() {
        if (this.weatherSound) this.weatherSound.play(true);
        if (this.ambienceSound) this.ambienceSound.play(true);
    }

    lap(position) {
        return Math.max(1, Math.floor(position / this.lapDistance) + 1);
    }

    indexAt(position) {
        let pos = position % this.lapDistance;
        if (pos < 0) pos += this.lapDistance;
        for (let i = 0; i < this.segments.length; i++)
            if (pos < this.starts[i] + this.segments[i].length) return i;
        return this.segments.length - 1;
    }

    // Road boundaries at a total distance (may span laps). Mirrors Track::roadComputer.
    roadAt(position) {
        const lap = Math.floor(position / this.lapDistance);
        const pos = position - lap * this.lapDistance;
        let center = lap * this.lapCenter;
        for (let i = 0; i < this.segments.length; i++) {
            const s = this.segments[i];
            const start = this.starts[i];
            if (pos >= start && pos < start + s.length) {
                const rel = pos - start;
                const offset = rel * DRIFT[s.type];
                return {
                    left: center - this.laneWidth + offset,
                    right: center + this.laneWidth + offset,
                    surface: s.surface, type: s.type, length: s.length, index: i, relPos: rel
                };
            }
            center += s.length * DRIFT[s.type];
        }
        const s = this.segments[0];
        return { left: center - this.laneWidth, right: center + this.laneWidth, surface: s.surface, type: 'Straight', length: s.length, index: 0, relPos: 0 };
    }

    // Player road: also tracks the player's segment for the copilot. Mirrors Track::road.
    road(position) {
        const r = this.roadAt(position);
        this.prevRelPos = this.relPos;
        this.relPos = r.relPos;
        this.currentRoad = r.index;
        return r;
    }

    // Returns the next segment the copilot should call, or null. Mirrors Track::nextRoad.
    nextRoad(position, speed, mode) {
        const n = this.segments.length;
        if (mode === 0) {
            const currentLength = this.segments[this.currentRoad].length;
            if (this.relPos + CALL_LENGTH > currentLength && this.prevRelPos + CALL_LENGTH <= currentLength)
                return this.segments[(this.currentRoad + 1) % n];
            return null;
        }
        const ahead = this.indexAt(position + CALL_LENGTH + speed / 2);
        const delta = (ahead - this.lastCalled + n) % n;
        if (delta > 0 && delta <= n / 2) {
            this.lastCalled = ahead;
            return this.segments[ahead];
        }
        return null;
    }

    // Track noises along the road (crowds, rivers, jets ...). Mirrors Track::run.
    run(position) {
        if (this.noisePlaying && position > this.noiseEnd) this.noisePlaying = false;
        const seg = this.segments[this.currentRoad];
        const noise = seg.noise;
        if (NOISE_LOOPS[noise]) {
            if (!this.noisePlaying) {
                let len = 0;
                for (let i = this.currentRoad; i < this.segments.length && this.segments[i].noise === noise; i++)
                    len += this.segments[i].length;
                this.noiseLength = Math.max(1, len);
                this.noisePlaying = true;
                this.noiseStart = position;
                this.noiseEnd = position + this.noiseLength;
            }
            let f = (position - this.noiseStart) / this.noiseLength;
            f = f < 0.5 ? f * 2 : 2 * (1 - f);
            const snd = this.sounds[noise];
            if (snd) {
                snd.volume(80 + f * 20);
                if (!snd.playing) {
                    snd.pan(NOISE_PAN[noise] || 0);
                    snd.play(true);
                }
            }
            for (const n of Object.keys(NOISE_LOOPS))
                if (n !== noise && this.sounds[n] && this.sounds[n].playing) this.sounds[n].stop();
        } else if (NOISE_ONESHOTS[noise]) {
            const snd = this.sounds[noise];
            if (snd && !snd.playing) snd.play();
        } else {
            for (const n of Object.keys(NOISE_LOOPS))
                if (this.sounds[n] && this.sounds[n].playing) this.sounds[n].stop();
        }
    }

    // Turn numbering for "approaching turn N" reports (remake's Track.TryGetTurn).
    turnInfo(position) {
        const idx = this.indexAt(position);
        let count = 0;
        const numbers = this.segments.map(s => (s.type !== 'Straight' ? ++count : 0));
        if (!count) return null;
        if (numbers[idx]) return { turn: numbers[idx], inTurn: true };
        for (let i = idx + 1; i < this.segments.length; i++)
            if (numbers[i]) return { turn: numbers[i], inTurn: false };
        return { turn: 1, inTurn: false, lapEnd: true };
    }

    dispose() {
        for (const s of Object.values(this.sounds)) s.dispose();
        if (this.weatherSound) this.weatherSound.dispose();
        if (this.ambienceSound) this.ambienceSound.dispose();
    }
}
