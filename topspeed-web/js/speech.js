// Spoken output. The desktop/mobile game talks through a screen reader or its own TTS; in the
// browser we offer the Web Speech API (self-voicing) and an ARIA live region for VoiceOver /
// TalkBack users who prefer their own screen reader. Settings pick one or both.

export class Speech {
    constructor(liveRegion, settings) {
        this.live = liveRegion;
        this.settings = settings;
        this.synth = window.speechSynthesis || null;
        this.voice = null;
        this._toggle = false;
        if (this.synth) {
            const pick = () => {
                const voices = this.synth.getVoices();
                const lang = (navigator.language || 'en').slice(0, 2);
                this.voice = voices.find(v => v.default && v.lang.startsWith(lang))
                    || voices.find(v => v.lang.startsWith(lang))
                    || voices[0] || null;
            };
            pick();
            if ('onvoiceschanged' in this.synth) this.synth.onvoiceschanged = pick;
        }
    }

    get usesTts() { return this.settings.speechMode !== 'screenreader' && !!this.synth; }
    get usesLive() { return this.settings.speechMode !== 'tts'; }

    // Called once from a user gesture: iOS only allows speech after one is spoken inside a gesture.
    unlock() {
        if (!this.synth) return;
        const u = new SpeechSynthesisUtterance(' ');
        u.volume = 0;
        this.synth.speak(u);
    }

    say(text, { interrupt = true } = {}) {
        if (!text) return;
        if (this.usesLive && this.live) {
            // Alternate a zero-width character so identical consecutive messages are re-announced.
            this._toggle = !this._toggle;
            this.live.textContent = text + (this._toggle ? '​' : '');
        }
        if (this.usesTts) {
            if (interrupt) this.synth.cancel();
            const u = new SpeechSynthesisUtterance(text);
            if (this.voice) u.voice = this.voice;
            u.rate = this.settings.speechRate;
            u.volume = Math.max(0, Math.min(1, this.settings.volSpeech / 100));
            this.synth.speak(u);
        }
    }

    stop() {
        if (this.synth) this.synth.cancel();
    }
}
