package com.teamtalk.webbypanel.server

import android.content.Context
import java.io.BufferedOutputStream
import java.io.BufferedReader
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.URLDecoder
import java.nio.charset.StandardCharsets
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Serves the built web panel from inside the app, over loopback only.
 *
 * The panel is a PWA: it needs a real HTTP origin for its manifest, its service
 * worker (a secure context — `http://127.0.0.1` qualifies) and its
 * `localStorage`. A `file://` or `data:` URL gives none of those, so the wrapper
 * runs a tiny static server on `127.0.0.1` instead of using
 * `loadDataWithBaseURL`.
 *
 * It serves exactly one thing: files out of the APK's `assets/panel/` tree,
 * which `scripts/android-panel-assets.ts` fills from the Vite build. Requests
 * for anything else get a JSON 404, so the panel's own health probe against its
 * own origin fails fast and it stays in simulated mode rather than hanging.
 *
 * Bound to the loopback address, so nothing off the device can reach it.
 */
class PanelServer(
    private val context: Context,
    private val bootScript: () -> String,
    private val ports: IntRange = DEFAULT_PORTS,
) {
    companion object {
        /** Where the built panel lands inside the APK. */
        const val ASSET_ROOT = "panel"

        /** Generated on the fly rather than read from assets. */
        const val BOOT_PATH = "_webby-boot.js"

        /**
         * A narrow, stable range. The port is the panel's origin, and
         * `localStorage` (the panel's own settings) is per-origin — so binding
         * the same port on every launch is what makes settings survive a
         * restart. Beyond this range we would rather fail than silently hand the
         * page a new identity.
         */
        val DEFAULT_PORTS = 8788..8807

        private const val LOOPBACK = "127.0.0.1"
        private const val BACKLOG = 24
        private const val THREADS = 8
        private const val REQUEST_TIMEOUT_MS = 10_000
        private const val MAX_HEADER_CHARS = 16_384
        private const val JSON_TYPE = "application/json; charset=utf-8"
        private const val JS_TYPE = "text/javascript; charset=utf-8"
        private const val HTML_TYPE = "text/html; charset=utf-8"
        private const val NO_STORE = "no-store"
        private const val IMMUTABLE = "public, max-age=31536000, immutable"
        private const val INDEX = "index.html"

        /**
         * `connect-src` has to allow plain http so live mode can reach a bridge
         * on the local network. `script-src` allows inline because a bundler may
         * emit a small inline bootstrap — and this origin is loopback-only, so
         * there is no untrusted content to protect the page from.
         */
        private val SECURITY_HEADERS = buildString {
            append("X-Content-Type-Options: nosniff\r\n")
            append("Referrer-Policy: no-referrer\r\n")
            append("Content-Security-Policy: default-src 'self'; img-src 'self' data:; ")
            append("style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; ")
            append("connect-src 'self' http: https:; worker-src 'self'; ")
            append("frame-ancestors 'none'; base-uri 'none'\r\n")
        }
    }

    private val running = AtomicBoolean(false)
    private var server: ServerSocket? = null
    private var executor: ExecutorService? = null
    private var acceptThread: Thread? = null

    /** The bound port, or 0 before [start] has succeeded. */
    @Volatile
    var port: Int = 0
        private set

    /** Bind a port and start accepting. Returns the port that was bound. */
    fun start(): Int {
        if (running.get()) return port

        val bound = bind() ?: throw IOException(
            "no free port in ${ports.first}..${ports.last} for the panel server",
        )
        server = bound
        port = bound.localPort
        running.set(true)

        executor = Executors.newFixedThreadPool(THREADS) { runnable ->
            Thread(runnable, "webby-panel-http").apply { isDaemon = true }
        }
        acceptThread = Thread({ acceptLoop(bound) }, "webby-panel-accept").apply {
            isDaemon = true
            start()
        }
        return port
    }

    /** Stop accepting and close the listening socket. Safe to call twice. */
    fun stop() {
        running.set(false)
        try {
            server?.close()
        } catch (_: IOException) {
            // Already closed by the accept loop; nothing to do.
        }
        server = null
        acceptThread = null
        executor?.shutdownNow()
        executor = null
    }

    private fun bind(): ServerSocket? {
        val loopback = InetAddress.getByName(LOOPBACK)
        for (candidate in ports) {
            try {
                return ServerSocket(candidate, BACKLOG, loopback)
            } catch (_: IOException) {
                // In use (or reserved by the OS): try the next one.
            }
        }
        return null
    }

    private fun acceptLoop(listening: ServerSocket) {
        while (running.get() && !listening.isClosed) {
            val client = try {
                listening.accept()
            } catch (_: IOException) {
                break
            }
            val pool = executor
            if (pool == null) {
                closeQuietly(client)
                break
            }
            try {
                pool.execute { handle(client) }
            } catch (_: RejectedExecutionException) {
                closeQuietly(client)
            }
        }
    }

    private fun closeQuietly(socket: Socket) {
        try {
            socket.close()
        } catch (_: IOException) {
            // Nothing useful to report.
        }
    }

    private fun handle(client: Socket) {
        try {
            client.use { open ->
                open.soTimeout = REQUEST_TIMEOUT_MS
                val reader = BufferedReader(InputStreamReader(open.getInputStream(), Charsets.ISO_8859_1))
                val requestLine = reader.readLine() ?: return
                drainHeaders(reader)

                val out = BufferedOutputStream(open.getOutputStream())
                val parts = requestLine.split(' ')
                if (parts.size < 2) {
                    respond(out, 400, "Bad Request", false, JSON_TYPE, NO_STORE, "{}")
                    return
                }

                val method = parts[0].uppercase()
                val target = parts[1]
                if (method != "GET" && method != "HEAD") {
                    respond(
                        out,
                        405,
                        "Method Not Allowed",
                        false,
                        JSON_TYPE,
                        NO_STORE,
                        """{"error":"this origin is read-only"}""",
                    )
                    return
                }

                route(target, out, method == "HEAD")
            }
        } catch (_: IOException) {
            // The WebView drops connections whenever it likes; not an error.
        }
    }

    /** Read (and ignore) the header block so the client is not left waiting. */
    private fun drainHeaders(reader: BufferedReader) {
        var budget = MAX_HEADER_CHARS
        while (budget > 0) {
            val line = reader.readLine() ?: return
            if (line.isEmpty()) return
            budget -= line.length
        }
    }

    private fun route(target: String, out: OutputStream, head: Boolean) {
        val rawPath = target.substringBefore('?').substringBefore('#')
        val path = try {
            URLDecoder.decode(rawPath, "UTF-8")
        } catch (_: IllegalArgumentException) {
            rawPath
        }

        // Everything is served out of the asset tree, so there is nothing to
        // escape into — but reject traversal outright rather than rely on that.
        if (path.contains("..") || path.contains('\u0000')) {
            notFound(out, head)
            return
        }

        var relative = path.trimStart('/')
        if (relative.isEmpty() || relative.endsWith("/")) relative += INDEX

        when {
            relative == BOOT_PATH -> respond(
                out, 200, "OK", head, JS_TYPE, NO_STORE, bootScript(),
            )

            relative == INDEX -> {
                val html = readAsset(INDEX)
                if (html == null) respond(out, 200, "OK", head, HTML_TYPE, NO_STORE, missingAssetsPage())
                else respond(out, 200, "OK", head, HTML_TYPE, NO_STORE, injectBoot(html))
            }

            else -> {
                val bytes = readAsset(relative)
                if (bytes == null) notFound(out, head)
                else respond(out, 200, "OK", head, Mime.forPath(relative), cacheFor(relative), bytes)
            }
        }
    }

    /**
     * Load the wrapper's seed script before the panel's module bundle runs, as a
     * separate file so the page needs no inline script from us.
     */
    private fun injectBoot(html: String): String {
        if (html.contains(BOOT_PATH)) return html
        val tag = "<script src=\"/$BOOT_PATH\"></script>"
        val head = html.indexOf("</head>")
        return if (head >= 0) html.substring(0, head) + tag + html.substring(head) else tag + html
    }

    private fun readAsset(relative: String): ByteArray? = try {
        context.assets.open("$ASSET_ROOT/$relative").use { it.readBytes() }
    } catch (_: IOException) {
        // Missing file, or a directory: both are "not found" here.
        null
    }

    /** Vite fingerprints everything under `assets/`, so those can be cached hard. */
    private fun cacheFor(relative: String): String =
        if (relative.startsWith("assets/")) IMMUTABLE else NO_STORE

    private fun notFound(out: OutputStream, head: Boolean) {
        respond(
            out,
            404,
            "Not Found",
            head,
            JSON_TYPE,
            NO_STORE,
            """{"error":"the wrapper serves the bundled panel only; /api/* lives on the bridge, not here"}""",
        )
    }

    private fun respond(
        out: OutputStream,
        status: Int,
        reason: String,
        head: Boolean,
        contentType: String,
        cacheControl: String,
        body: String,
    ) = respond(out, status, reason, head, contentType, cacheControl, body.toByteArray(StandardCharsets.UTF_8))

    private fun respond(
        out: OutputStream,
        status: Int,
        reason: String,
        head: Boolean,
        contentType: String,
        cacheControl: String,
        body: ByteArray,
    ) {
        val header = buildString {
            append("HTTP/1.1 ").append(status).append(' ').append(reason).append("\r\n")
            append("Content-Type: ").append(contentType).append("\r\n")
            append("Content-Length: ").append(body.size).append("\r\n")
            append("Cache-Control: ").append(cacheControl).append("\r\n")
            append(SECURITY_HEADERS)
            append("Connection: close\r\n")
            append("\r\n")
        }
        out.write(header.toByteArray(StandardCharsets.ISO_8859_1))
        if (!head) out.write(body)
        out.flush()
    }

    /** Shown when the APK was built without running the asset copy. */
    private fun missingAssetsPage(): String = """
        <!doctype html>
        <html lang="en"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Panel assets missing</title></head>
        <body style="margin:0;padding:32px;background:#0B1220;color:#DCE6F5;
                     font:15px/1.6 system-ui,-apple-system,sans-serif">
        <h1 style="font-size:18px;color:#7DD3FC">This APK has no panel inside it</h1>
        <p>The wrapper serves the built web panel from <code>assets/panel/</code>, and that
        directory is empty in this build.</p>
        <p>Build the panel and copy it in, then rebuild the APK:</p>
        <pre style="padding:12px;background:#111C2E;border-radius:8px;overflow:auto">bun install
        bun run android:assets   # vite build -&gt; android-panel/app/src/main/assets/panel
        gradle -p android-panel assembleDebug</pre>
        <p style="color:#8DA2BF">The release workflow does both steps for you.</p>
        </body></html>
    """.trimIndent()
}
