// Audio menu system modelled on the original game's MenuManager/MenuScreen: a stack of screens,
// navigation sounds from Sounds/menu/<preset>, spoken item announcements, and the same touch
// gesture bindings as the mobile build (Menu/Runtime/Screen/Input.cs). Every screen is also
// rendered as real HTML controls so browser screen readers and mouse/keyboard users can use it.

import { G } from './gestures.js';

export const A = {
    Prev: 'prev', Next: 'next', Home: 'home', End: 'end', Left: 'left', Right: 'right',
    PageUp: 'pageup', PageDown: 'pagedown', Activate: 'activate', Back: 'back', Repeat: 'repeat'
};

export class MenuManager {
    constructor({ container, audio, speech, settings }) {
        this.container = container;
        this.audio = audio;
        this.speech = speech;
        this.settings = settings;
        this.screens = new Map();
        this.stack = [];   // [{ screen, index }]
        this.music = null;
        this.musicFile = null;
        this.active = false;
    }

    register(screen) {
        this.screens.set(screen.id, screen);
        return screen;
    }

    get current() { return this.stack[this.stack.length - 1] || null; }

    // Show a screen (by id or object) on top of the stack.
    push(idOrScreen, { silentTitle = false } = {}) {
        const screen = typeof idOrScreen === 'string' ? this.screens.get(idOrScreen) : idOrScreen;
        if (!screen) return;
        if (screen.onOpen) screen.onOpen();
        this.stack.push({ screen, index: 0 });
        this._render();
        this._announceOpen(silentTitle);
    }

    // Replace the whole stack with one screen (e.g. back to the main menu after a race).
    reset(id) {
        this.stack = [];
        this.push(id);
    }

    pop() {
        if (this.stack.length <= 1) return false;
        this.stack.pop();
        this._render();
        this._announceItem(true);
        return true;
    }

    show() {
        this.active = true;
        this.container.hidden = false;
        this._render();
    }

    hide() {
        this.active = false;
        this.container.hidden = true;
    }

    // ---- music -------------------------------------------------------------------------
    async playMusic(file) {
        if (this.musicFile === file && this.music && this.music.playing) return;
        this.stopMusic();
        this.musicFile = file;
        this.music = this.audio.create(file, 'music');
        await this.music.ready;
        if (this.musicFile === file) this.music.play(true);
    }

    stopMusic() {
        if (this.music) this.music.dispose();
        this.music = null;
        this.musicFile = null;
    }

    // ---- input -------------------------------------------------------------------------
    gesture(intent) {
        const cur = this.current;
        if (!cur) return;
        const item = this._item();
        const isSlider = item && item.type === 'slider';
        const adjustable = item && (item.type === 'slider' || item.type === 'choice' || item.type === 'check');
        switch (intent) {
            case G.SwipeLeft: return this.action(A.Prev);
            case G.SwipeRight: return this.action(A.Next);
            case G.SwipeUp: return isSlider ? undefined : this.action(A.Activate);
            case G.SwipeDown: return this.action(A.Back);
            case G.TwoFingerSwipeUp: return this.action(isSlider ? A.PageUp : A.Home);
            case G.TwoFingerSwipeDown: return this.action(isSlider ? A.PageDown : A.End);
            case G.ThreeFingerSwipeUp: return isSlider ? this.action(A.Home) : undefined;
            case G.ThreeFingerSwipeDown: return isSlider ? this.action(A.End) : undefined;
            case G.TwoFingerSwipeLeft: return adjustable ? this.action(A.Left) : undefined;
            case G.TwoFingerSwipeRight: return adjustable ? this.action(A.Right) : undefined;
            // Additions for the browser port: tap re-reads, double tap activates (screen-reader style).
            case G.Tap: return this.action(A.Repeat);
            case G.DoubleTap: return isSlider ? undefined : this.action(A.Activate);
            default: return undefined;
        }
    }

    key(e) {
        const map = {
            ArrowUp: A.Prev, ArrowDown: A.Next, Home: A.Home, End: A.End,
            ArrowLeft: A.Left, ArrowRight: A.Right, PageUp: A.PageUp, PageDown: A.PageDown,
            Enter: A.Activate, ' ': A.Activate, Escape: A.Back, Backspace: A.Back
        };
        const action = map[e.key];
        if (!action) return false;
        e.preventDefault();
        this.action(action);
        return true;
    }

    action(action) {
        const cur = this.current;
        if (!cur) return;
        const items = this._items();
        const item = items[cur.index];
        switch (action) {
            case A.Prev: return this._move(cur.index - 1);
            case A.Next: return this._move(cur.index + 1);
            // On a slider HOME/END jump to maximum/minimum, as in the original.
            case A.Home: return item && item.type === 'slider' ? this._adjust(item, 1000) : this._move(0, true);
            case A.End: return item && item.type === 'slider' ? this._adjust(item, -1000) : this._move(items.length - 1, true);
            case A.Repeat: return this._announceItem(true);
            case A.Left: return this._adjust(item, -1);
            case A.Right: return this._adjust(item, 1);
            case A.PageUp: return this._adjust(item, 10);
            case A.PageDown: return this._adjust(item, -10);
            case A.Activate: return this._activate(item);
            case A.Back: return this._back();
        }
    }

    // ---- internals ---------------------------------------------------------------------
    _items() {
        const cur = this.current;
        if (!cur) return [];
        const items = typeof cur.screen.items === 'function' ? cur.screen.items() : cur.screen.items;
        return items.filter(i => !i.hidden || !i.hidden());
    }

    _item() {
        const cur = this.current;
        return cur ? this._items()[cur.index] : null;
    }

    _sound(name, index, count) {
        const pan = this.settings.menuPanning && count > 1 ? ((index / (count - 1)) * 2 - 1) * 60 : 0;
        this.audio.playOnce(`menu/${this.settings.menuSounds}/menu_${name}.wav`, 'menu', { pan });
    }

    _move(target, jump = false) {
        const cur = this.current;
        const items = this._items();
        if (!items.length) return;
        let idx = target;
        let sound = 'navigate';
        if (idx < 0 || idx >= items.length) {
            if (this.settings.menuWrap && !jump) {
                idx = idx < 0 ? items.length - 1 : 0;
                sound = 'wrap';
            } else {
                idx = Math.max(0, Math.min(items.length - 1, idx));
                this._sound('edge', idx, items.length);
                this._announceItem(true);
                return;
            }
        }
        cur.index = idx;
        this._sound(sound, idx, items.length);
        this._syncDom();
        this._announceItem(true);
    }

    _adjust(item, delta) {
        if (!item) return;
        if (item.type === 'slider') {
            const step = Math.abs(delta) >= 10 ? (item.bigStep || item.step * 10) : item.step;
            let v = Math.abs(delta) >= 1000 ? (delta > 0 ? item.max : item.min) : item.get() + Math.sign(delta) * step;
            v = Math.max(item.min, Math.min(item.max, v));
            v = Math.round(v / item.step) * item.step;
            item.set(Number(v.toFixed(3)));
        } else if (item.type === 'choice') {
            const n = item.options.length;
            item.set((item.get() + (delta > 0 ? 1 : -1) + n) % n);
        } else if (item.type === 'check') {
            item.set(!item.get());
        } else {
            return;
        }
        this._sound('navigate', this.current.index, this._items().length);
        this._syncDom();
        this.speech.say(valueText(item));
    }

    _activate(item) {
        if (!item) return;
        if (item.type === 'check') return this._adjust(item, 1);
        if (item.type === 'choice') return this._adjust(item, 1);
        if (item.type === 'slider' || item.type === 'text') return this._announceItem(true);
        this._sound('enter', this.current.index, this._items().length);
        if (item.run) item.run();
        if (item.next) this.push(item.next);
    }

    _back() {
        const cur = this.current;
        if (!cur) return;
        if (cur.screen.onBack) {
            cur.screen.onBack();
            return;
        }
        if (this.stack.length > 1) {
            this._sound('enter', cur.index, this._items().length);
            this.pop();
        }
    }

    _announceOpen(silentTitle) {
        const cur = this.current;
        const title = typeof cur.screen.title === 'function' ? cur.screen.title() : cur.screen.title;
        const item = this._item();
        const parts = [];
        if (title && !silentTitle) parts.push(title);
        if (item) parts.push(itemText(item, cur.index, this._items().length));
        this.speech.say(parts.join('. '));
    }

    _announceItem(interrupt) {
        const item = this._item();
        if (item) this.speech.say(itemText(item, this.current.index, this._items().length), { interrupt });
    }

    _render() {
        const cur = this.current;
        const root = this.container;
        root.innerHTML = '';
        if (!cur) return;
        const title = typeof cur.screen.title === 'function' ? cur.screen.title() : cur.screen.title;
        const h = document.createElement('h1');
        h.textContent = title || 'Top Speed';
        root.appendChild(h);
        const list = document.createElement('div');
        list.className = 'menu-list';
        list.setAttribute('role', 'group');
        list.setAttribute('aria-label', title || 'Menu');
        this._items().forEach((item, i) => {
            let el;
            if (item.type === 'slider') {
                el = document.createElement('label');
                el.className = 'menu-item slider';
                const span = document.createElement('span');
                const input = document.createElement('input');
                input.type = 'range';
                input.min = item.min;
                input.max = item.max;
                input.step = item.step;
                input.value = item.get();
                input.addEventListener('input', () => {
                    item.set(Number(input.value));
                    span.textContent = valueText(item);
                });
                input.addEventListener('focus', () => this._focusIndex(i));
                span.textContent = valueText(item);
                el.append(span, input);
            } else {
                el = document.createElement('button');
                el.type = 'button';
                el.className = 'menu-item ' + item.type;
                el.textContent = itemText(item, i, 0, false);
                if (item.type === 'check') {
                    el.setAttribute('role', 'checkbox');
                    el.setAttribute('aria-checked', String(!!item.get()));
                    el.textContent = labelText(item);
                }
                el.addEventListener('click', () => {
                    this._focusIndex(i);
                    this._activate(item);
                });
                el.addEventListener('focus', () => this._focusIndex(i));
            }
            el.dataset.index = i;
            list.appendChild(el);
        });
        root.appendChild(list);
        this._syncDom();
    }

    _focusIndex(i) {
        const cur = this.current;
        if (cur && cur.index !== i) {
            cur.index = i;
            this._syncDom();
        }
    }

    _syncDom() {
        const cur = this.current;
        if (!cur) return;
        const items = this._items();
        const els = this.container.querySelectorAll('.menu-item');
        els.forEach((el, i) => {
            const item = items[i];
            el.classList.toggle('selected', i === cur.index);
            if (!item) return;
            if (item.type === 'check') el.setAttribute('aria-checked', String(!!item.get()));
            else if (item.type === 'slider') {
                el.querySelector('span').textContent = valueText(item);
                el.querySelector('input').value = item.get();
            } else el.textContent = itemText(item, i, 0, false);
        });
        const sel = els[cur.index];
        if (sel && sel.scrollIntoView) sel.scrollIntoView({ block: 'nearest' });
        // Keep keyboard / screen reader focus on the selected item.
        const target = sel && (sel.querySelector('input') || sel);
        if (target && this.active && document.activeElement !== target) {
            try { target.focus({ preventScroll: true }); } catch (_) { target.focus(); }
        }
    }
}

function labelText(item) {
    return typeof item.label === 'function' ? item.label() : item.label;
}

export function valueText(item) {
    const label = labelText(item);
    switch (item.type) {
        case 'check': return `${label}, ${item.get() ? 'checked' : 'not checked'}`;
        case 'choice': return `${label}: ${item.options[item.get()]}`;
        case 'slider': return `${label}: ${item.format ? item.format(item.get()) : item.get()}`;
        default: return label;
    }
}

function itemText(item, index, count, withPosition = true) {
    let text = valueText(item);
    if (item.type === 'check') text += ', check box';
    else if (item.type === 'slider') text += ', slider';
    else if (item.type === 'choice') text += ', option';
    if (withPosition && count > 1) text += `, ${index + 1} of ${count}`;
    return text;
}
