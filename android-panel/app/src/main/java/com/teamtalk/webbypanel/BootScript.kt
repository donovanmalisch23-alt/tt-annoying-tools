package com.teamtalk.webbypanel

/**
 * The wrapper's seed script, injected into `index.html` before the panel's own
 * bundle runs.
 *
 * The panel keeps its settings in `localStorage` under `tt-web.bridge.v1`
 * (`{mode, base, token, username}` — see `src/app/live/store.ts`). Typing a
 * bridge address on a phone keyboard is miserable, so the wrapper can point the
 * panel at a bridge once and be done with it.
 *
 * The rules, in full:
 *  - no address configured: the script does nothing, and the panel keeps
 *    whatever it has (simulated mode by default);
 *  - an address configured, different from the last one we applied: write
 *    `mode: "live"` and the address, leaving any admin token and username alone;
 *  - an address configured, already applied: do nothing, so switching the panel
 *    back to simulated mode actually sticks.
 *
 * Changing the address in the wrapper, or clearing panel data, re-applies it.
 */
object BootScript {
    private const val STORAGE_KEY = "tt-web.bridge.v1"

    /** Records the address we last applied, so we do not fight the operator. */
    private const val SEED_KEY = "tt-web.bridge.seed"

    fun forBridgeUrl(url: String): String {
        val base = url.trim()
        return buildString {
            append("(function(){try{")
            append("var K=").append(literal(STORAGE_KEY))
            append(",S=").append(literal(SEED_KEY))
            append(",B=").append(literal(base))
            append(";if(!B){localStorage.removeItem(S);return;}")
            append("if(localStorage.getItem(S)===B)return;")
            append("var c={};try{c=JSON.parse(localStorage.getItem(K)||\"{}\")||{};}catch(e){c={};}")
            append("c.mode=\"live\";c.base=B;")
            append("localStorage.setItem(K,JSON.stringify(c));")
            append("localStorage.setItem(S,B);")
            append("}catch(e){}})();")
        }
    }

    /** A JavaScript string literal — the address is operator input, so escape it. */
    private fun literal(value: String): String {
        val builder = StringBuilder(value.length + 2)
        builder.append('"')
        for (character in value) {
            when {
                character == '\\' -> builder.append("\\\\")
                character == '"' -> builder.append("\\\"")
                character == '\n' -> builder.append("\\n")
                character == '\r' -> builder.append("\\r")
                character == '\t' -> builder.append("\\t")
                character == '<' -> builder.append("\\u003c")
                character.code < 0x20 -> builder.append("\\u%04x".format(character.code))
                else -> builder.append(character)
            }
        }
        builder.append('"')
        return builder.toString()
    }
}
