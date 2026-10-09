// Touch gesture recognition matching the gestures of the original game's mobile build
// (TopSpeed/Input/Gestures): one-, two- and three-finger swipes, single/double/triple taps with
// one to three fingers, and long press. The screen can be split into zones (as the race screen is);
// each zone recognises gestures independently and also exposes its live touch state, which the
// race uses for the drag-to-drive controls.

export const G = {
    SwipeLeft: 'SwipeLeft', SwipeRight: 'SwipeRight', SwipeUp: 'SwipeUp', SwipeDown: 'SwipeDown',
    Tap: 'Tap', DoubleTap: 'DoubleTap', TripleTap: 'TripleTap', LongPress: 'LongPress',
    TwoFingerTap: 'TwoFingerTap', TwoFingerDoubleTap: 'TwoFingerDoubleTap', TwoFingerTripleTap: 'TwoFingerTripleTap',
    TwoFingerSwipeLeft: 'TwoFingerSwipeLeft', TwoFingerSwipeRight: 'TwoFingerSwipeRight',
    TwoFingerSwipeUp: 'TwoFingerSwipeUp', TwoFingerSwipeDown: 'TwoFingerSwipeDown',
    ThreeFingerTap: 'ThreeFingerTap', ThreeFingerDoubleTap: 'ThreeFingerDoubleTap', ThreeFingerTripleTap: 'ThreeFingerTripleTap',
    ThreeFingerSwipeLeft: 'ThreeFingerSwipeLeft', ThreeFingerSwipeRight: 'ThreeFingerSwipeRight',
    ThreeFingerSwipeUp: 'ThreeFingerSwipeUp', ThreeFingerSwipeDown: 'ThreeFingerSwipeDown'
};

const TAP_MAX_MOVE = 0.035;       // fraction of the shorter screen side
const SWIPE_MIN_MOVE = 0.07;
const TAP_MAX_MS = 450;
const MULTI_TAP_GAP_MS = 320;
const LONG_PRESS_MS = 650;

export class GestureInput {
    constructor(target, onGesture) {
        this.target = target;
        this.onGesture = onGesture;
        this.zones = [{ id: 'screen', x: 0, y: 0, w: 1, h: 1 }];
        this.sessions = new Map();   // zoneId -> session
        this.touchZone = new Map();  // touch identifier -> zoneId
        this.pendingTaps = new Map();// zoneId -> {fingers, count, timer}
        this.enabled = true;
        const opts = { passive: false };
        target.addEventListener('touchstart', e => this._start(e), opts);
        target.addEventListener('touchmove', e => this._move(e), opts);
        target.addEventListener('touchend', e => this._end(e), opts);
        target.addEventListener('touchcancel', e => this._end(e, true), opts);
    }

    setZones(zones) {
        this.zones = zones && zones.length ? zones : [{ id: 'screen', x: 0, y: 0, w: 1, h: 1 }];
        this.reset();
    }

    reset() {
        for (const s of this.sessions.values()) clearTimeout(s.longTimer);
        for (const p of this.pendingTaps.values()) clearTimeout(p.timer);
        this.sessions.clear();
        this.touchZone.clear();
        this.pendingTaps.clear();
    }

    // Live state of a zone: whether fingers are down, how many, and where the first finger
    // started and is now (normalised 0..1 screen coordinates).
    zoneState(zoneId) {
        const s = this.sessions.get(zoneId);
        if (!s || s.active.size === 0) return { active: false, fingerCount: 0 };
        const first = s.active.get(s.firstId) || s.active.values().next().value;
        return {
            active: true,
            fingerCount: s.active.size,
            startX: first.sx, startY: first.sy, x: first.x, y: first.y,
            duration: performance.now() - s.startTime
        };
    }

    _norm(t) {
        return { x: t.clientX / window.innerWidth, y: t.clientY / window.innerHeight };
    }

    _zoneAt(p) {
        let best = this.zones[0];
        for (const z of this.zones)
            if (p.x >= z.x && p.x < z.x + z.w && p.y >= z.y && p.y < z.y + z.h) return z;
        return best;
    }

    _start(e) {
        if (!this.enabled) return;
        e.preventDefault();
        for (const t of e.changedTouches) {
            const p = this._norm(t);
            const zone = this._zoneAt(p);
            this.touchZone.set(t.identifier, zone.id);
            let s = this.sessions.get(zone.id);
            if (!s || s.active.size === 0) {
                s = {
                    active: new Map(), lifted: [], maxFingers: 0, startTime: performance.now(),
                    firstId: t.identifier, longFired: false, longTimer: null
                };
                this.sessions.set(zone.id, s);
            }
            s.active.set(t.identifier, { sx: p.x, sy: p.y, x: p.x, y: p.y });
            s.maxFingers = Math.max(s.maxFingers, s.active.size);
            clearTimeout(s.longTimer);
            if (s.maxFingers === 1) {
                s.longTimer = setTimeout(() => {
                    const f = s.active.get(s.firstId);
                    if (s.active.size === 1 && f && this._dist(f) < TAP_MAX_MOVE * 1.5) {
                        s.longFired = true;
                        this._emit(G.LongPress, zone.id);
                    }
                }, LONG_PRESS_MS);
            }
        }
    }

    _move(e) {
        if (!this.enabled) return;
        e.preventDefault();
        for (const t of e.changedTouches) {
            const zoneId = this.touchZone.get(t.identifier);
            const s = this.sessions.get(zoneId);
            const f = s && s.active.get(t.identifier);
            if (!f) continue;
            const p = this._norm(t);
            f.x = p.x;
            f.y = p.y;
        }
    }

    _end(e, cancelled = false) {
        if (!this.enabled) return;
        e.preventDefault();
        for (const t of e.changedTouches) {
            const zoneId = this.touchZone.get(t.identifier);
            this.touchZone.delete(t.identifier);
            const s = this.sessions.get(zoneId);
            const f = s && s.active.get(t.identifier);
            if (!f) continue;
            const p = this._norm(t);
            f.x = p.x;
            f.y = p.y;
            s.active.delete(t.identifier);
            s.lifted.push(f);
            if (s.active.size === 0) {
                clearTimeout(s.longTimer);
                if (!cancelled && !s.longFired) this._classify(zoneId, s);
            }
        }
    }

    // Distances are measured in units of the shorter screen side so thresholds feel the same
    // in portrait and landscape.
    _scale() {
        const short = Math.min(window.innerWidth, window.innerHeight);
        return { x: window.innerWidth / short, y: window.innerHeight / short };
    }

    _dist(f) {
        const { x: scaleX, y: scaleY } = this._scale();
        return Math.hypot((f.x - f.sx) * scaleX, (f.y - f.sy) * scaleY);
    }

    _classify(zoneId, s) {
        const n = s.lifted.length;
        const fingers = Math.min(3, s.maxFingers);
        const { x: scaleX, y: scaleY } = this._scale();
        let dx = 0, dy = 0;
        for (const f of s.lifted) {
            dx += (f.x - f.sx) * scaleX;
            dy += (f.y - f.sy) * scaleY;
        }
        dx /= n;
        dy /= n;
        const dist = Math.hypot(dx, dy);
        const duration = performance.now() - s.startTime;
        if (dist >= SWIPE_MIN_MOVE) {
            const horizontal = Math.abs(dx) > Math.abs(dy);
            const dir = horizontal ? (dx < 0 ? 'Left' : 'Right') : (dy < 0 ? 'Up' : 'Down');
            const prefix = fingers >= 3 ? 'ThreeFinger' : fingers === 2 ? 'TwoFinger' : '';
            this._emit(prefix + 'Swipe' + dir, zoneId);
            return;
        }
        if (dist <= TAP_MAX_MOVE && duration <= TAP_MAX_MS) this._tap(zoneId, fingers);
    }

    _tap(zoneId, fingers) {
        let p = this.pendingTaps.get(zoneId);
        if (p && p.fingers === fingers) {
            clearTimeout(p.timer);
            p.count++;
        } else {
            if (p) { clearTimeout(p.timer); this._flushTap(zoneId, p); }
            p = { fingers, count: 1, timer: null };
            this.pendingTaps.set(zoneId, p);
        }
        if (p.count >= 3) {
            this.pendingTaps.delete(zoneId);
            this._flushTap(zoneId, p);
            return;
        }
        p.timer = setTimeout(() => {
            this.pendingTaps.delete(zoneId);
            this._flushTap(zoneId, p);
        }, MULTI_TAP_GAP_MS);
    }

    _flushTap(zoneId, p) {
        const prefix = p.fingers >= 3 ? 'ThreeFinger' : p.fingers === 2 ? 'TwoFinger' : '';
        const kind = p.count >= 3 ? 'TripleTap' : p.count === 2 ? 'DoubleTap' : 'Tap';
        this._emit(prefix + kind, zoneId);
    }

    _emit(intent, zoneId) {
        try { this.onGesture(intent, zoneId); } catch (err) { console.error(err); }
    }
}
