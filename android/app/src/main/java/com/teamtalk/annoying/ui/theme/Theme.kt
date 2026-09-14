package com.teamtalk.annoying.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val AnnoyingDark = darkColorScheme(
    primary = Color(0xFF5EEAD4),
    onPrimary = Color(0xFF04211C),
    primaryContainer = Color(0xFF113A34),
    onPrimaryContainer = Color(0xFF9DF3E4),
    secondary = Color(0xFFF0B429),
    onSecondary = Color(0xFF2A1C00),
    secondaryContainer = Color(0xFF3A2C08),
    onSecondaryContainer = Color(0xFFFFE0A3),
    background = Color(0xFF0B1220),
    onBackground = Color(0xFFE2E8F0),
    surface = Color(0xFF111A2C),
    onSurface = Color(0xFFE2E8F0),
    surfaceVariant = Color(0xFF1B2740),
    onSurfaceVariant = Color(0xFFA9B7D0),
    outline = Color(0xFF39486B),
    error = Color(0xFFF87171),
    onError = Color(0xFF2A0A0A),
)

/** Always dark: this is a tester tool, and the theme matches the suite's look. */
@Composable
fun ToolsTheme(content: @Composable () -> Unit) {
    MaterialTheme(colorScheme = AnnoyingDark, content = content)
}
