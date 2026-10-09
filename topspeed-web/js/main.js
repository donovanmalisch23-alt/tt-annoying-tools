// Top Speed web: application shell. Builds the menu tree of the original game
// (Menu/Build/*.cs), runs the logo, and hands control to races.

import { AudioEngine } from './audio.js';
import { Speech } from './speech.js';
import { GestureInput } from './gestures.js';
import { MenuManager } from './menu.js';
import { loadSettings, saveSettings, resetSettings } from './settings.js';
import { TRACK_DATA } from './data-tracks.js';
import { VEHICLES } from './data-vehicles.js';
import { Race } from './race.js';
import { HELP } from './help.js';

const RACE_TRACKS = [
    ['america', 'America'], ['austria', 'Austria'], ['belgium', 'Belgium'], ['brazil', 'Brazil'],
    ['china', 'China'], ['england', 'England'], ['finland', 'Finland'], ['france', 'France'],
    ['germany', 'Germany'], ['ireland', 'Ireland'], ['italy', 'Italy'], ['netherlands', 'Netherlands'],
    ['portugal', 'Portugal'], ['russia', 'Russia'], ['spain', 'Spain'], ['sweden', 'Sweden'],
    ['switserland', 'Switserland']
];
const ADVENTURE_TRACKS = [
    ['advHills', 'Rally hills'], ['advCoast', 'French coast'], ['advCountry', 'English country'],
    ['advAirport', 'Ride airport'], ['advDesert', 'Rally desert'], ['advRush', 'Rush hour'],
    ['advEscape', 'Polar escape']
];
const RACE_BUSES = ['engine', 'events', 'others', 'surface', 'ambience', 'voice'];
const pickRandom = arr => arr[Math.floor(Math.random() * arr.length)];

class App {
    constructor() {
        this.settings = loadSettings();
        this.audio = new AudioEngine();
        this.speech = new Speech(document.getElementById('live'), this.settings);
        this.el = {
            splash: document.getElementById('splash'),
            start: document.getElementById('start-button'),
            menu: document.getElementById('menu'),
            race: document.getElementById('race'),
            hud: document.getElementById('hud'),
            loading: document.getElementById('loading')
        };
        this.menu = new MenuManager({ container: this.el.menu, audio: this.audio, speech: this.speech, settings: this.settings });
        this.gestures = new GestureInput(document.body, (intent, zone) => this.onGesture(intent, zone));
        this.gestures.enabled = false;
        this.race = null;
        this.raceMenuOpen = false;
        this.setup = { mode: 'single', category: 'race', trackKey: null, trackName: null, vehicleIndex: 0 };
        this.buildMenus();
        this.bindEvents();
    }

    // ---- startup -----------------------------------------------------------------------
    bindEvents() {
        this.el.start.addEventListener('click', () => this.boot());
        // Touching anywhere on the splash also starts (gesture layer is off until then).
        this.el.splash.addEventListener('touchend', e => { e.preventDefault(); this.boot(); }, { passive: false });
        document.addEventListener('keydown', e => this.onKeyDown(e));
        document.addEventListener('keyup', e => { if (this.race) this.race.keyUp(e); });
        document.addEventListener('visibilitychange', () => {
            if (document.hidden && this.race && !this.race.paused && !this.race.finishedAll) this.race.pause();
            if (!document.hidden && this.audio.ctx && this.audio.ctx.state !== 'running') this.audio.ctx.resume();
        });
        window.addEventListener('deviceorientation', e => this.onOrientation(e));
    }

    async boot() {
        if (this.booted) return;
        this.booted = true;
        await this.audio.unlock();
        this.audio.setVolumes(this.settings);
        this.speech.unlock();
        this.el.splash.hidden = true;
        this.gestures.enabled = true;
        this.gestures.setZones(null);
        if (this.settings.playLogo) await this.playLogo();
        this.openMainMenu();
    }

    playLogo() {
        return new Promise(async resolve => {
            const logo = this.audio.create('Legacy/pitd_logo.wav', 'menu');
            await logo.ready;
            let done = false;
            const finish = () => {
                if (done) return;
                done = true;
                logo.dispose();
                this.skipLogo = null;
                resolve();
            };
            this.skipLogo = finish;
            logo.play();
            setTimeout(finish, Math.max(500, logo.length * 1000 + 200));
        });
    }

    openMainMenu() {
        this.menu.show();
        this.menu.reset('main');
        this.menu.playMusic('En/Music/theme1.ogg');
    }

    // ---- input routing -----------------------------------------------------------------
    onGesture(intent, zone) {
        if (this.skipLogo) return this.skipLogo();
        if (this.race && !this.raceMenuOpen) return this.race.gesture(intent, zone);
        if (this.menu.active) this.menu.gesture(intent);
    }

    onKeyDown(e) {
        if (!this.booted) {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); this.boot(); }
            return;
        }
        if (this.skipLogo) { e.preventDefault(); return this.skipLogo(); }
        if (this.race && !this.raceMenuOpen) {
            if (e.key.startsWith('Arrow') || e.key === ' ' || e.key === 'Escape') e.preventDefault();
            return this.race.keyDown(e);
        }
        if (this.menu.active) this.menu.key(e);
    }

    onOrientation(e) {
        if (!this.race || !this.settings.motionSteering || e.beta === null) return;
        const angle = (screen.orientation && screen.orientation.angle) || window.orientation || 0;
        // Tilt like a steering wheel: in landscape that is the beta axis, in portrait gamma.
        let tilt;
        if (angle === 90) tilt = e.beta;
        else if (angle === -90 || angle === 270) tilt = -e.beta;
        else tilt = e.gamma;
        const m = this.race.motion;
        if (m.neutral === null) m.neutral = tilt;
        let d = tilt - m.neutral;
        if (Math.abs(d) < 2) d = 0;
        m.steering = Math.max(-100, Math.min(100, (d / 24) * 100));
    }

    async requestMotionPermission() {
        const D = window.DeviceOrientationEvent;
        if (D && typeof D.requestPermission === 'function') {
            try {
                const res = await D.requestPermission();
                if (res !== 'granted') {
                    this.speech.say('Motion access was denied. Motion steering stays off.');
                    return false;
                }
            } catch (_) {
                this.speech.say('Motion sensors need the page to be opened over HTTPS on iOS.');
                return false;
            }
        }
        return true;
    }

    // ---- menus -------------------------------------------------------------------------
    buildMenus() {
        const m = this.menu;
        const s = this.settings;
        const save = () => { saveSettings(s); this.audio.setVolumes(s); };
        const act = (label, run, next) => ({ type: 'action', label, run, next });
        const check = (label, key, after) => ({ type: 'check', label, get: () => !!s[key], set: v => { s[key] = v; save(); if (after) after(v); } });
        const choice = (label, key, options) => ({ type: 'choice', label, options, get: () => s[key], set: v => { s[key] = v; save(); } });
        const slider = (label, key, min, max, step, format) => ({ type: 'slider', label, min, max, step, bigStep: step * 10, get: () => s[key], set: v => { s[key] = v; save(); }, format });
        const back = () => act('Back', () => m.pop());
        const speechModes = ['tts', 'screenreader', 'both'];

        m.register({
            id: 'main',
            title: 'Main menu',
            items: [
                act('Quick start', () => this.quickStart()),
                act('Time trial', () => { this.setup.mode = 'time'; }, 'track_type'),
                act('Single race', () => { this.setup.mode = 'single'; }, 'track_type'),
                act('MultiPlayer game', () => this.speech.say('Multiplayer needs the Top Speed server and is not available in the web version.')),
                act('Options', null, 'options_main'),
                act('Help', null, 'help'),
                act('Check for updates', () => this.speech.say('The web version is always up to date. Reload the page to get the latest version.')),
                act('About', () => this.speech.say(HELP.about)),
                act('Exit Game', () => this.exitGame())
            ],
            onBack: () => this.speech.say('Main menu. Choose Exit Game to quit.')
        });

        m.register({
            id: 'track_type',
            title: 'Choose track type',
            items: [
                act('Race track', () => { this.setup.category = 'race'; }, 'tracks_race'),
                act('Street adventure', () => { this.setup.category = 'adventure'; }, 'tracks_adventure'),
                act('Random', () => {
                    this.setup.category = Math.random() < 0.5 ? 'race' : 'adventure';
                    m.push(this.setup.category === 'race' ? 'tracks_race' : 'tracks_adventure');
                }),
                back()
            ]
        });

        const trackMenu = (id, list) => m.register({
            id,
            title: 'Select a track',
            items: [
                ...list.map(([key, name]) => act(name, () => this.selectTrack(key, name), 'vehicles')),
                act('Random', () => { const [k, n] = pickRandom(list); this.selectTrack(k, n); }, 'vehicles'),
                back()
            ]
        });
        trackMenu('tracks_race', RACE_TRACKS);
        trackMenu('tracks_adventure', ADVENTURE_TRACKS);

        m.register({
            id: 'vehicles',
            title: 'Select a vehicle',
            items: [
                ...VEHICLES.map((v, i) => act(v.name, () => { this.setup.vehicleIndex = i; }, 'transmission')),
                act('Random', () => { this.setup.vehicleIndex = Math.floor(Math.random() * VEHICLES.length); }, 'transmission'),
                back()
            ]
        });

        m.register({
            id: 'transmission',
            title: 'Select transmission mode',
            items: [
                act('Automatic', () => this.startRace(false)),
                act('Manual', () => this.startRace(true)),
                back()
            ]
        });

        // Options -------------------------------------------------------------------------
        m.register({
            id: 'options_main',
            title: 'Options',
            items: [
                act('General', null, 'options_game'),
                act('Race settings', null, 'options_race'),
                act('Controls', null, 'options_controls'),
                act('Speech', null, 'options_speech'),
                act('Volume settings', null, 'options_volume'),
                act('Restore default settings', () => {
                    resetSettings(s);
                    this.audio.setVolumes(s);
                    this.speech.say('All settings restored to their defaults.');
                }),
                back()
            ]
        });

        m.register({
            id: 'options_game',
            title: 'General',
            items: [
                choice('Units', 'units', ['metric', 'imperial']),
                check('Play logo at startup', 'playLogo'),
                check('Enable menu wrapping', 'menuWrap'),
                check('Enable menu navigation panning', 'menuPanning'),
                // Presets are the folders Sounds/menu/1 and Sounds/menu/2.
                { type: 'choice', label: 'Menu sounds', options: ['preset 1', 'preset 2'], get: () => s.menuSounds - 1, set: v => { s.menuSounds = v + 1; save(); } },
                back()
            ]
        });

        m.register({
            id: 'options_race',
            title: 'Race settings',
            items: [
                choice('Copilot', 'copilot', ['off', 'curves only', 'all']),
                choice('Curve announcement method', 'curveAnnouncement', ['fixed distance', 'speed dependent']),
                choice('Curve announcements', 'curveCues', ['spoken', 'tones']),
                choice('Automatic race information', 'automaticInfo', ['off', 'laps only', 'all']),
                slider('Number of laps', 'laps', 1, 16, 1),
                slider('Number of computer players', 'computerPlayers', 1, 9, 1),
                choice('Single race difficulty', 'difficulty', ['easy', 'normal', 'hard']),
                back()
            ]
        });

        m.register({
            id: 'options_controls',
            title: 'Controls',
            items: [
                choice('Touch driving layout', 'touchLayout', ['original: drag sideways for throttle and brake, up and down to steer', 'alternative: drag up and down for throttle and brake, sideways to steer']),
                check('Motion steering', 'motionSteering', async v => {
                    if (v && !(await this.requestMotionPermission())) { s.motionSteering = false; save(); }
                }),
                check('Vibration feedback', 'vibration'),
                act('Gesture guide', null, 'help_gestures'),
                back()
            ]
        });

        m.register({
            id: 'options_speech',
            title: 'Speech',
            items: [
                { type: 'choice', label: 'Speech output', options: ['built-in voice', 'screen reader only', 'both'], get: () => Math.max(0, speechModes.indexOf(s.speechMode)), set: v => { s.speechMode = speechModes[v]; save(); } },
                slider('Speech rate', 'speechRate', 0.5, 3, 0.1, v => v.toFixed(1)),
                slider('Speech volume', 'volSpeech', 0, 100, 5),
                act('Test speech', () => this.speech.say('Top Speed is ready to race.')),
                back()
            ]
        });

        m.register({
            id: 'options_volume',
            title: 'Volume settings',
            items: [
                slider('Master audio volume', 'volMaster', 0, 100, 5),
                slider('Music volume', 'volMusic', 0, 100, 5),
                slider('Vehicle engine sounds', 'volEngine', 0, 100, 5),
                slider('Vehicle event sounds', 'volEvents', 0, 100, 5),
                slider('Other vehicles sounds', 'volOthers', 0, 100, 5),
                slider('Surface loop sounds', 'volSurface', 0, 100, 5),
                slider('Ambients and sound sources', 'volAmbience', 0, 100, 5),
                slider('Copilot and race information', 'volVoice', 0, 100, 5),
                slider('Menu sounds', 'volMenu', 0, 100, 5),
                back()
            ]
        });

        // Help -----------------------------------------------------------------------------
        const textMenu = (id, title, lines) => m.register({
            id, title, items: [...lines.map(l => ({ type: 'text', label: l })), back()]
        });
        m.register({
            id: 'help',
            title: 'Help',
            items: [
                act('Game guide', null, 'help_guide'),
                act('Touch gestures', null, 'help_gestures'),
                act('Keyboard keys', null, 'help_keys'),
                act("What's new in the web version", null, 'help_web'),
                back()
            ]
        });
        textMenu('help_guide', 'Game guide', HELP.guide);
        textMenu('help_gestures', 'Touch gestures', HELP.gestures);
        textMenu('help_keys', 'Keyboard keys', HELP.keys);
        textMenu('help_web', "What's new in the web version", HELP.web);

        // In-race menu (opened by swiping down in the top-left zone, or Escape).
        m.register({
            id: 'race_menu',
            title: 'Race paused',
            items: [
                act('Resume race', () => this.closeRaceMenu()),
                act('Restart race', () => this.restartRace()),
                act('Quit to main menu', () => this.quitRace()),
            ],
            onBack: () => this.closeRaceMenu()
        });
    }

    selectTrack(key, name) {
        this.setup.trackKey = key;
        this.setup.trackName = name;
    }

    quickStart() {
        const [key, name] = pickRandom(RACE_TRACKS);
        this.setup.mode = 'single';
        this.selectTrack(key, name);
        this.setup.vehicleIndex = Math.floor(Math.random() * VEHICLES.length);
        this.startRace(false);
    }

    exitGame() {
        this.speech.say('Goodbye.');
        this.menu.stopMusic();
        this.menu.hide();
        this.booted = false;
        this.gestures.enabled = false;
        this.el.splash.hidden = false;
        this.el.start.focus();
    }

    // ---- races -------------------------------------------------------------------------
    async startRace(manual) {
        const { mode, trackKey, trackName, vehicleIndex } = this.setup;
        if (this.race) this.endRace();
        this.lastRace = { manual };
        this.menu.stopMusic();
        this.menu.hide();
        this.el.loading.hidden = false;
        this.el.loading.textContent = 'Loading…';
        const vehicle = VEHICLES[vehicleIndex];
        this.speech.say(`${trackName}. ${vehicle.name}. ${manual ? 'Manual' : 'Automatic'} transmission. Loading.`);
        const race = new Race(this, { mode, trackKey, trackName, trackData: TRACK_DATA[trackKey], vehicleIndex, manual });
        this.race = race;
        try {
            await race.load(p => { this.el.loading.textContent = `Loading… ${Math.round(p * 100)}%`; });
        } catch (err) {
            console.error(err);
            this.speech.say('The race could not be loaded.');
            this.race = null;
            this.el.loading.hidden = true;
            this.openMainMenu();
            return;
        }
        if (this.race !== race) return;
        this.el.loading.hidden = true;
        this.el.race.hidden = false;
        this.raceMenuOpen = false;
        race.setZones(this.gestures);
        this.requestWakeLock();
        race.begin();
        this.lastFrame = performance.now();
        const loop = now => {
            if (this.race !== race) return;
            const dt = (now - this.lastFrame) / 1000;
            this.lastFrame = now;
            race.update(dt);
            requestAnimationFrame(loop);
        };
        requestAnimationFrame(loop);
    }

    muteRaceBuses(mute) {
        if (!this.audio.ctx) return;
        if (mute) for (const b of RACE_BUSES) this.audio.buses[b].gain.value = 0;
        else this.audio.setVolumes(this.settings);
    }

    openRaceMenu() {
        if (!this.race || this.race.finishedAll) return;
        this.race.pause();
        this.raceMenuOpen = true;
        this.gestures.setZones(null);
        this.menu.show();
        this.menu.reset('race_menu');
    }

    closeRaceMenu() {
        if (!this.race) return;
        this.raceMenuOpen = false;
        this.menu.hide();
        this.race.setZones(this.gestures);
        this.race.resume();
        this.speech.say('Resumed');
    }

    endRace() {
        if (this.race) this.race.dispose();
        this.race = null;
        this.raceMenuOpen = false;
        this.el.race.hidden = true;
        this.gestures.setZones(null);
        this.audio.setVolumes(this.settings);
        this.releaseWakeLock();
    }

    restartRace() {
        this.endRace();
        this.startRace(this.lastRace ? this.lastRace.manual : false);
    }

    quitRace() {
        this.endRace();
        this.openMainMenu();
    }

    finishRace(race, lines) {
        // Let the result announcement play, then show the results screen.
        setTimeout(() => {
            if (this.race !== race) return;
            this.endRace();
            this.menu.register({
                id: 'results',
                title: 'Race results',
                items: [
                    ...lines.map(l => ({ type: 'text', label: l })),
                    { type: 'action', label: 'Race again', run: () => this.startRace(this.lastRace.manual) },
                    { type: 'action', label: 'Main menu', run: () => this.openMainMenu() }
                ],
                onBack: () => this.openMainMenu()
            });
            this.menu.show();
            this.menu.reset('results');
            this.menu.playMusic('En/Music/theme1.ogg');
        }, 6000);
    }

    updateHud(race) {
        const now = performance.now();
        if (this._hudAt && now - this._hudAt < 150) return;
        this._hudAt = now;
        const p = race.player;
        const kmh = p.speed / 100;
        const speed = this.settings.units === 1 ? `${Math.round(kmh * 0.621371)} mph` : `${Math.round(kmh)} km/h`;
        const lap = Math.min(race.laps, Math.max(1, race.lap));
        const parts = [
            `<div class="hud-speed">${speed}</div>`,
            `<div>Gear ${p.gear}${p.manual ? '' : ' (auto)'}</div>`,
            `<div>Lap ${lap} / ${race.laps}</div>`
        ];
        if (race.mode === 'single') parts.push(`<div>Position ${race.position} / ${race.bots.length + 1}</div>`);
        parts.push(`<div class="hud-lane"><span style="left:${Math.max(0, Math.min(100, p.relPos * 100))}%"></span></div>`);
        this.el.hud.innerHTML = parts.join('');
    }

    async requestWakeLock() {
        try {
            if (navigator.wakeLock) this.wakeLock = await navigator.wakeLock.request('screen');
        } catch (_) { /* not available */ }
    }

    releaseWakeLock() {
        try { if (this.wakeLock) this.wakeLock.release(); } catch (_) { /* ignored */ }
        this.wakeLock = null;
    }
}

window.topSpeed = new App();
