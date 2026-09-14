package com.teamtalk.webbypanel

import android.app.Activity
import android.app.AlertDialog
import android.content.ActivityNotFoundException
import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.text.InputType
import android.view.KeyEvent
import android.view.View
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.WebResourceRequest
import android.webkit.WebSettings
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.EditText
import android.widget.FrameLayout
import android.widget.TextView
import android.widget.Toast
import com.teamtalk.webbypanel.server.PanelServer
import java.io.IOException

/**
 * A shell around the web panel.
 *
 * It starts [PanelServer], which serves the panel bundled in the APK over
 * `http://127.0.0.1:<port>`, and points a WebView at it. Serving it over a
 * loopback origin (rather than `file://`) is what makes the panel behave as a
 * PWA: the manifest resolves, the service worker registers — `127.0.0.1` counts
 * as a secure context — and `localStorage` persists the operator's settings.
 *
 * The panel runs exactly as it does in a browser: the in-tab server simulator
 * needs nothing else. Live mode still needs the Python bridge, which cannot run
 * on Android, so it points at a bridge on the local network via the *Bridge*
 * action. Nothing here talks to a TeamTalk server directly.
 */
class MainActivity : Activity() {
    private lateinit var webView: WebView
    private lateinit var header: TextView
    private lateinit var prefs: PanelPrefs

    private var server: PanelServer? = null
    private var origin: String = ""
    private var startupFailure: String? = null

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_main)

        prefs = PanelPrefs(this)
        header = findViewById(R.id.header_title)
        webView = findViewById(R.id.web_view)

        WebView.setWebContentsDebuggingEnabled(BuildConfig.DEBUG)
        configureWebView()

        findViewById<Button>(R.id.action_reload).setOnClickListener { webView.reload() }
        findViewById<Button>(R.id.action_bridge).setOnClickListener { editBridgeUrl() }
        findViewById<Button>(R.id.action_browser).setOnClickListener { openInBrowser() }

        startPanelServer()
    }

    // ----- server ----------------------------------------------------------- //

    private fun startPanelServer() {
        // The boot script is read per request, so changing the bridge address
        // takes effect on the next reload without restarting the server.
        // Named, because a trailing lambda would bind to the constructor's last
        // parameter (`ports`), not to `bootScript`.
        val panel = PanelServer(
            applicationContext,
            bootScript = { BootScript.forBridgeUrl(prefs.bridgeUrl) },
        )
        try {
            val bound = panel.start()
            server = panel
            origin = "http://127.0.0.1:$bound"
        } catch (error: IOException) {
            startupFailure = error.message
            showStartupFailure()
            return
        }

        header.text = getString(
            R.string.header_status,
            BuildConfig.RELEASE_CHANNEL,
            BuildConfig.VERSION_NAME,
            origin.removePrefix("http://"),
        )
        webView.loadUrl("$origin/")
    }

    private fun showStartupFailure() {
        val detail = startupFailure ?: getString(R.string.startup_failed_unknown)
        header.text = getString(
            R.string.header_status,
            BuildConfig.RELEASE_CHANNEL,
            BuildConfig.VERSION_NAME,
            getString(R.string.startup_failed),
        )
        webView.loadDataWithBaseURL(null, failurePage(detail), "text/html", "utf-8", null)
    }

    private fun failurePage(detail: String): String = """
        <!doctype html>
        <html lang="en"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width,initial-scale=1">
        <title>Panel did not start</title></head>
        <body style="margin:0;padding:32px;background:#0B1220;color:#DCE6F5;
                     font:15px/1.6 system-ui,-apple-system,sans-serif">
        <h1 style="font-size:18px;color:#7DD3FC">The panel server did not start</h1>
        <p>${escapeHtml(detail)}</p>
        <p style="color:#8DA2BF">Another app is holding ports 8788-8807, or the sandbox
        blocked the loopback bind. Close the other app and reopen this one.</p>
        </body></html>
    """.trimIndent()

    // ----- web view --------------------------------------------------------- //

    private fun configureWebView() {
        webView.setBackgroundColor(PANEL_BACKGROUND)
        webView.settings.apply {
            javaScriptEnabled = true
            // localStorage: the panel's settings, plus the wrapper's bridge seed.
            domStorageEnabled = true
            databaseEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mediaPlaybackRequiresUserGesture = true
            cacheMode = WebSettings.LOAD_DEFAULT
            useWideViewPort = true
            loadWithOverviewMode = false
            builtInZoomControls = false
            displayZoomControls = false
            textZoom = 100
        }
        webView.isScrollbarFadingEnabled = true
        webView.overScrollMode = View.OVER_SCROLL_NEVER

        webView.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url ?: return false
                val target = url.toString()
                // Anything on the panel's own origin (or the panel) stays here;
                // everything else is somebody else's and belongs in a browser.
                if (origin.isNotEmpty() && target.startsWith(origin)) return false
                openExternally(url)
                return true
            }
        }
    }

    override fun onKeyDown(keyCode: Int, event: KeyEvent): Boolean {
        if (keyCode == KeyEvent.KEYCODE_BACK && webView.canGoBack()) {
            webView.goBack()
            return true
        }
        return super.onKeyDown(keyCode, event)
    }

    override fun onPause() {
        webView.onPause()
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        webView.onResume()
    }

    override fun onDestroy() {
        server?.stop()
        server = null
        webView.stopLoading()
        (webView.parent as? ViewGroup)?.removeView(webView)
        webView.destroy()
        super.onDestroy()
    }

    // ----- actions ---------------------------------------------------------- //

    private fun editBridgeUrl() {
        val input = EditText(this).apply {
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setText(prefs.bridgeUrl)
            hint = getString(R.string.bridge_hint)
            setSingleLine(true)
        }
        val padding = (resources.displayMetrics.density * 20).toInt()
        val container = FrameLayout(this).apply {
            setPadding(padding, padding / 2, padding, 0)
            addView(
                input,
                FrameLayout.LayoutParams(
                    FrameLayout.LayoutParams.MATCH_PARENT,
                    FrameLayout.LayoutParams.WRAP_CONTENT,
                ),
            )
        }

        AlertDialog.Builder(this)
            .setTitle(R.string.bridge_dialog_title)
            .setMessage(R.string.bridge_dialog_message)
            .setView(container)
            .setPositiveButton(android.R.string.ok) { _, _ ->
                val value = input.text.toString().trim()
                prefs.bridgeUrl = value
                toast(if (value.isEmpty()) R.string.bridge_cleared else R.string.bridge_saved)
                webView.reload()
            }
            .setNeutralButton(R.string.action_reset) { _, _ -> resetPanelData() }
            .setNegativeButton(android.R.string.cancel, null)
            .show()
    }

    private fun resetPanelData() {
        webView.clearCache(true)
        webView.clearHistory()
        CookieManager.getInstance().removeAllCookies(null)
        // Wipes localStorage, so the wrapper's seed is applied again on reload.
        WebStorage.getInstance().deleteAllData()
        prefs.bridgeUrl = ""
        toast(R.string.reset_done)
        webView.reload()
    }

    private fun openInBrowser() {
        if (origin.isEmpty()) {
            toast(R.string.startup_failed)
            return
        }
        openExternally(Uri.parse("$origin/"))
    }

    private fun openExternally(uri: Uri) {
        try {
            startActivity(Intent(Intent.ACTION_VIEW, uri))
        } catch (_: ActivityNotFoundException) {
            toast(R.string.no_browser)
        }
    }

    private fun toast(message: Int) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private fun escapeHtml(value: String): String = value
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")

    private companion object {
        /** Matches the panel's own theme colour, so there is no white flash. */
        val PANEL_BACKGROUND: Int = Color.parseColor("#0B1220")
    }
}
