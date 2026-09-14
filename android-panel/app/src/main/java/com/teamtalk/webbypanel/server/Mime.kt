package com.teamtalk.webbypanel.server

/** Content types for the handful of file kinds a Vite build emits. */
object Mime {
    private val TYPES = mapOf(
        "html" to "text/html; charset=utf-8",
        "js" to "text/javascript; charset=utf-8",
        "mjs" to "text/javascript; charset=utf-8",
        "css" to "text/css; charset=utf-8",
        "json" to "application/json; charset=utf-8",
        "webmanifest" to "application/manifest+json; charset=utf-8",
        "svg" to "image/svg+xml",
        "png" to "image/png",
        "jpg" to "image/jpeg",
        "jpeg" to "image/jpeg",
        "gif" to "image/gif",
        "webp" to "image/webp",
        "ico" to "image/x-icon",
        "woff" to "font/woff",
        "woff2" to "font/woff2",
        "ttf" to "font/ttf",
        "map" to "application/json; charset=utf-8",
        "txt" to "text/plain; charset=utf-8",
    )

    fun forPath(path: String): String =
        TYPES[path.substringAfterLast('.', "").lowercase()] ?: "application/octet-stream"
}
