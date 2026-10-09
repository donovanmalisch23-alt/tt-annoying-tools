// Persistent settings (browser localStorage). Defaults follow the original game's defaults.

const KEY = 'topspeed-web.settings.v1';
const RECORDS_KEY = 'topspeed-web.records.v1';

export const DEFAULTS = {
    // Race settings
    copilot: 2,               // 0 off, 1 curves only, 2 all (curves and surfaces)
    curveAnnouncement: 1,     // 0 fixed distance, 1 speed dependent
    curveCues: 0,             // 0 spoken copilot, 1 tones
    automaticInfo: 2,         // 0 off, 1 laps only, 2 all
    laps: 3,
    computerPlayers: 5,
    difficulty: 1,            // 0 easy, 1 normal, 2 hard
    units: 0,                 // 0 metric, 1 imperial
    // Touch / driving
    touchLayout: 0,           // 0 original (drag sideways = throttle/brake, up/down = steer), 1 alternative
    motionSteering: false,
    vibration: true,
    // Game
    playLogo: true,
    menuWrap: false,
    menuPanning: true,
    menuSounds: 1,            // preset folder under Sounds/menu
    // Speech
    speechMode: 'tts',        // 'tts' | 'screenreader' | 'both'
    speechRate: 1.2,
    // Volumes (0..100)
    volMaster: 100,
    volMusic: 60,
    volEngine: 100,
    volEvents: 100,
    volOthers: 100,
    volSurface: 100,
    volAmbience: 100,
    volVoice: 100,
    volMenu: 100,
    volSpeech: 100
};

export function loadSettings() {
    let stored = {};
    try { stored = JSON.parse(localStorage.getItem(KEY) || '{}') || {}; } catch (_) { /* private mode */ }
    return Object.assign({}, DEFAULTS, stored);
}

export function saveSettings(settings) {
    try { localStorage.setItem(KEY, JSON.stringify(settings)); } catch (_) { /* private mode */ }
}

export function resetSettings(settings) {
    Object.assign(settings, DEFAULTS);
    saveSettings(settings);
}

export function getRecord(trackKey) {
    try { return (JSON.parse(localStorage.getItem(RECORDS_KEY) || '{}') || {})[trackKey] || null; } catch (_) { return null; }
}

export function setRecord(trackKey, ms) {
    try {
        const all = JSON.parse(localStorage.getItem(RECORDS_KEY) || '{}') || {};
        all[trackKey] = ms;
        localStorage.setItem(RECORDS_KEY, JSON.stringify(all));
    } catch (_) { /* private mode */ }
}
