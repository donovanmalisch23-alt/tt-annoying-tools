// Web Audio engine modelled on the DirectSound-style API the original game used:
// sounds have a frequency (Hz, relative to the file's native sample rate), a pan (-100..100)
// and a volume (0..100).

const SOUND_ROOT = 'assets/Sounds/';

export const BUSES = ['music', 'engine', 'events', 'others', 'surface', 'ambience', 'voice', 'menu'];

export class AudioEngine {
    constructor() {
        this.ctx = null;
        this.buffers = new Map();   // path -> Promise<{buffer, rate}>
        this.decoded = new Map();   // path -> {buffer, rate} once loaded
        this.buses = {};
        this.master = null;
        this.oggSupported = true;
    }

    // Must be called from a user gesture (tap / key press) so iOS and Android allow audio.
    async unlock() {
        if (!this.ctx) {
            try {
                // Safari 17+: let Web Audio play even when the ringer switch is on silent.
                if (navigator.audioSession) navigator.audioSession.type = 'playback';
            } catch (_) { /* not supported */ }
            const Ctx = window.AudioContext || window.webkitAudioContext;
            this.ctx = new Ctx({ latencyHint: 'interactive' });
            this.master = this.ctx.createGain();
            this.master.connect(this.ctx.destination);
            for (const name of BUSES) {
                const g = this.ctx.createGain();
                g.connect(this.master);
                this.buses[name] = g;
            }
            const probe = document.createElement('audio');
            this.oggSupported = probe.canPlayType('audio/ogg; codecs="vorbis"') !== '';
        }
        if (this.ctx.state !== 'running') {
            try { await this.ctx.resume(); } catch (_) { /* retried on next gesture */ }
        }
        // Play one silent sample: older iOS only unlocks after a buffer actually starts.
        const b = this.ctx.createBuffer(1, 1, 22050);
        const s = this.ctx.createBufferSource();
        s.buffer = b;
        s.connect(this.ctx.destination);
        s.start(0);
    }

    get now() { return this.ctx ? this.ctx.currentTime : 0; }

    setVolumes(settings) {
        if (!this.ctx) return;
        this.master.gain.value = percentToGain(settings.volMaster);
        const map = {
            music: settings.volMusic, engine: settings.volEngine, events: settings.volEvents,
            others: settings.volOthers, surface: settings.volSurface, ambience: settings.volAmbience,
            voice: settings.volVoice, menu: settings.volMenu
        };
        for (const [bus, vol] of Object.entries(map))
            this.buses[bus].gain.value = percentToGain(vol);
    }

    load(path) {
        if (!path) return Promise.resolve(null);
        if (this.buffers.has(path)) return this.buffers.get(path);
        const p = this._fetchDecode(path).then(d => { this.decoded.set(path, d); return d; }).catch(err => {
            console.warn('Sound failed to load', path, err);
            return null;
        });
        this.buffers.set(path, p);
        return p;
    }

    async _fetchDecode(path) {
        let url = SOUND_ROOT + path;
        // Safari before 17.4 cannot decode Ogg Vorbis; use the MP3 copy made from the same file.
        if (path.endsWith('.ogg') && !this.oggSupported) url = url.replace(/\.ogg$/, '.mp3');
        let data = await fetchArray(url);
        let rate = path.endsWith('.wav') ? wavSampleRate(data) : 0;
        let buffer;
        try {
            buffer = await decode(this.ctx, data);
        } catch (e) {
            if (!path.endsWith('.ogg') || url.endsWith('.mp3')) throw e;
            data = await fetchArray(url.replace(/\.ogg$/, '.mp3'));
            buffer = await decode(this.ctx, data);
        }
        return { buffer, rate: rate || buffer.sampleRate };
    }

    // Synchronous access to an already-loaded sound.
    peek(path) {
        return this.decoded.get(path) || null;
    }

    async loadAll(paths) {
        await Promise.all(paths.filter(Boolean).map(p => this.load(p)));
    }

    // Creates a sound object. Loading happens in the background; call load() first
    // (or loadAll) when the sound must be ready immediately.
    create(path, bus = 'events') {
        return new Sound(this, path, bus);
    }

    // Fire-and-forget one shot.
    async playOnce(path, bus = 'menu', { pan = 0, volume = 100, rate = 1 } = {}) {
        const s = this.create(path, bus);
        await s.ready;
        s.pan(pan);
        s.volume(volume);
        if (rate !== 1) s.playbackRate(rate);
        s.play();
        return s;
    }
}

export class Sound {
    constructor(engine, path, bus) {
        this.engine = engine;
        this.path = path;
        this.data = null;
        this.source = null;
        this.isPlaying = false;
        this.looping = false;
        this._rate = 1;
        this._startedAt = 0;
        const ctx = engine.ctx;
        this.gain = ctx.createGain();
        this.panner = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
        if (this.panner) {
            this.gain.connect(this.panner);
            this.panner.connect(engine.buses[bus] || engine.master);
        } else {
            this.gain.connect(engine.buses[bus] || engine.master);
        }
        this.ready = engine.load(path).then(d => { this.data = d; return this; });
    }

    get loaded() { return !!this.data; }
    get length() { return this.data ? this.data.buffer.duration : 0; }
    get playing() { return this.isPlaying; }

    play(loop = false) {
        if (!this.data) {
            // Not decoded yet: start as soon as it is, unless stopped meanwhile.
            this._pendingLoop = loop;
            this._pending = true;
            this.ready.then(() => { if (this._pending) { this._pending = false; this.play(this._pendingLoop); } });
            this.isPlaying = true;
            return;
        }
        this.stop();
        const src = this.engine.ctx.createBufferSource();
        src.buffer = this.data.buffer;
        src.loop = loop;
        src.playbackRate.value = this._rate;
        src.connect(this.gain);
        src.onended = () => {
            if (this.source === src) {
                this.isPlaying = false;
                this.source = null;
            }
        };
        src.start();
        this.source = src;
        this.looping = loop;
        this.isPlaying = true;
        this._startedAt = this.engine.now;
    }

    stop() {
        this._pending = false;
        if (this.source) {
            const src = this.source;
            this.source = null;
            try { src.stop(); } catch (_) { /* already stopped */ }
            src.disconnect();
        }
        this.isPlaying = false;
    }

    // Frequency in Hz, like DirectSound's SetFrequency.
    frequency(hz) {
        const base = this.data ? this.data.rate : 22050;
        this.playbackRate(Math.max(0.01, hz / base));
    }

    playbackRate(rate) {
        if (Math.abs(rate - this._rate) < Math.max(1e-4, this._rate * 0.002)) return;
        this._rate = rate;
        if (this.source) this.source.playbackRate.setTargetAtTime(rate, this.engine.now, 0.015);
    }

    // Skip unchanged values: these are called every frame and Safari slows down when the
    // automation timeline fills up with redundant events.
    pan(p) {
        const v = Math.max(-1, Math.min(1, p / 100));
        if (!this.panner || Math.abs(v - (this._pan ?? 2)) < 0.005) return;
        this._pan = v;
        this.panner.pan.setTargetAtTime(v, this.engine.now, 0.015);
    }

    volume(v) {
        const g = percentToGain(v);
        if (Math.abs(g - (this._gain ?? -1)) < 0.002) return;
        this._gain = g;
        this.gain.gain.setTargetAtTime(g, this.engine.now, 0.02);
    }

    dispose() {
        this.stop();
        this.gain.disconnect();
        if (this.panner) this.panner.disconnect();
    }
}

// The original's 0..100 volume scale behaved like an attenuation in decibels, where 100 is full
// level. Map it onto a ~45 dB range so 90 ("normal" for road loops) is only slightly quieter.
export function percentToGain(v) {
    if (v <= 0) return 0;
    if (v >= 100) return 1;
    return Math.pow(10, (-(100 - v) * 0.45) / 20);
}

async function fetchArray(url) {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status} for ${url}`);
    return res.arrayBuffer();
}

function decode(ctx, data) {
    // Old Safari only supports the callback form of decodeAudioData.
    return new Promise((resolve, reject) => {
        const p = ctx.decodeAudioData(data.slice(0), resolve, reject);
        if (p && p.catch) p.catch(reject);
    });
}

// decodeAudioData resamples to the context rate, so remember the file's own rate:
// the game's engine pitch math works in the file's native frequency.
function wavSampleRate(data) {
    try {
        const view = new DataView(data);
        if (view.getUint32(0, false) !== 0x52494646) return 0; // "RIFF"
        let offset = 12;
        while (offset + 8 <= view.byteLength) {
            const id = view.getUint32(offset, false);
            const size = view.getUint32(offset + 4, true);
            if (id === 0x666d7420) return view.getUint32(offset + 12, true); // "fmt "
            offset += 8 + size + (size & 1);
        }
    } catch (_) { /* malformed */ }
    return 0;
}
