package com.teamtalk.annoying.core

import android.util.Base64
import java.security.MessageDigest
import java.security.SecureRandom
import javax.crypto.SecretKeyFactory
import javax.crypto.spec.PBEKeySpec

/** The stored administrator credential. Never holds the password itself. */
data class AdminRecord(
    val username: String,
    val algorithm: String,
    val iterations: Int,
    val saltB64: String,
    val hashB64: String,
)

/**
 * Device-local administrator credential for the admin panel.
 *
 * This is the Android counterpart of the desktop bridge's gate in
 * `webby/admin.py`: PBKDF2 with a per-install random salt, a constant-time
 * comparison, and a lockout after repeated failures. It guards the server
 * allowlist, the target settings and the SDK license decision — the things a
 * tester should not be able to change on their own.
 */
class AdminAuth(private val store: ConfigStore) {

    enum class SignIn { OK, BAD_CREDENTIALS, LOCKED, NOT_CONFIGURED, BAD_INPUT }

    val configured: Boolean get() = store.loadAdminRecord() != null

    val username: String? get() = store.loadAdminRecord()?.username

    /** Seconds left on the lockout, or 0 when a sign-in may be attempted. */
    fun lockRemainingSeconds(now: Long = System.currentTimeMillis()): Long {
        val until = store.loadLockUntilMs()
        if (until <= now) return 0
        return ((until - now) + 999) / 1000
    }

    /** Creates (or replaces) the credential. Returns an error message, or null. */
    fun create(username: String, password: String, confirm: String): String? {
        val name = username.trim()
        if (name.isEmpty()) return "Pick an administrator name."
        if (name.length < MIN_USERNAME) {
            return "Use at least $MIN_USERNAME characters for the administrator name."
        }
        if (password.length < MIN_PASSWORD) {
            return "Use at least $MIN_PASSWORD characters for the password."
        }
        if (password != confirm) return "The two passwords do not match."

        val salt = ByteArray(SALT_BYTES).also { SecureRandom().nextBytes(it) }
        val (algorithm, factory) = deriveFactory()
        val hash = derive(factory, password, salt, ITERATIONS)
        store.saveAdminRecord(
            AdminRecord(
                username = name,
                algorithm = algorithm,
                iterations = ITERATIONS,
                saltB64 = encode(salt),
                hashB64 = encode(hash),
            ),
        )
        store.saveFailedAttempts(0)
        store.saveLockUntilMs(0)
        LogBus.log("[admin] Administrator '$name' provisioned on this device.")
        return null
    }

    fun signIn(username: String, password: String): SignIn {
        val record = store.loadAdminRecord() ?: return SignIn.NOT_CONFIGURED
        if (lockRemainingSeconds() > 0) return SignIn.LOCKED
        if (username.isBlank() || password.isEmpty()) return SignIn.BAD_INPUT

        val salt = runCatching { decode(record.saltB64) }.getOrNull()
        if (salt == null) return SignIn.BAD_CREDENTIALS
        val expected = runCatching { decode(record.hashB64) }.getOrNull()
        if (expected == null) return SignIn.BAD_CREDENTIALS

        val factory = runCatching {
            SecretKeyFactory.getInstance(record.algorithm)
        }.getOrNull() ?: deriveFactory().second
        val actual = derive(factory, password, salt, record.iterations)

        val userOk = constantTimeEquals(
            record.username.lowercase(),
            username.trim().lowercase(),
        )
        val passOk = MessageDigest.isEqual(expected, actual)
        if (userOk && passOk) {
            store.saveFailedAttempts(0)
            store.saveLockUntilMs(0)
            LogBus.log("[admin] Administrator '${record.username}' signed in.")
            return SignIn.OK
        }

        val failures = store.loadFailedAttempts() + 1
        if (failures >= MAX_ATTEMPTS) {
            store.saveFailedAttempts(0)
            store.saveLockUntilMs(System.currentTimeMillis() + LOCK_WINDOW_MS)
            LogBus.log("[admin] Too many failed sign-ins; locked for ${LOCK_WINDOW_MS / 1000} s.")
            return SignIn.LOCKED
        }
        store.saveFailedAttempts(failures)
        LogBus.log("[admin] Failed sign-in attempt ($failures/$MAX_ATTEMPTS).")
        return SignIn.BAD_CREDENTIALS
    }

    /** Forgets the credential so the next visit provisions a new one. */
    fun reset() {
        store.clearAdminRecord()
        store.saveFailedAttempts(0)
        store.saveLockUntilMs(0)
        LogBus.log("[admin] Administrator credential cleared on this device.")
    }

    private fun derive(factory: SecretKeyFactory, password: String, salt: ByteArray, rounds: Int): ByteArray {
        val spec = PBEKeySpec(password.toCharArray(), salt, rounds, KEY_BITS)
        return try {
            factory.generateSecret(spec).encoded
        } finally {
            spec.clearPassword()
        }
    }

    /**
     * PBKDF2-HMAC-SHA256 needs API 26; on 24/25 fall back to HMAC-SHA1, which
     * is still a sound PRF for password stretching. The choice is recorded with
     * the credential so verification always uses the same algorithm.
     */
    private fun deriveFactory(): Pair<String, SecretKeyFactory> {
        for (algorithm in listOf("PBKDF2WithHmacSHA256", "PBKDF2WithHmacSHA1")) {
            runCatching { SecretKeyFactory.getInstance(algorithm) }
                .getOrNull()
                ?.let { return algorithm to it }
        }
        throw TeamTalkConfigException("This device has no PBKDF2 provider.")
    }

    private fun constantTimeEquals(a: String, b: String): Boolean =
        MessageDigest.isEqual(a.toByteArray(), b.toByteArray())

    private fun encode(bytes: ByteArray): String = Base64.encodeToString(bytes, Base64.NO_WRAP)

    private fun decode(text: String): ByteArray = Base64.decode(text, Base64.NO_WRAP)

    private companion object {
        const val MIN_USERNAME = 3
        const val MIN_PASSWORD = 6
        const val SALT_BYTES = 16
        const val KEY_BITS = 256
        const val ITERATIONS = 120_000
        const val MAX_ATTEMPTS = 5
        const val LOCK_WINDOW_MS = 60_000L
    }
}
