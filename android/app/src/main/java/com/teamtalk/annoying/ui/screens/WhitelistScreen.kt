package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.teamtalk.annoying.core.Whitelist
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.SectionTitle

@Composable
fun WhitelistScreen(viewModel: AppViewModel) {
    var text by remember { mutableStateOf(viewModel.whitelistText) }
    var saved by remember { mutableStateOf(false) }
    val entries = remember(text) { Whitelist.parse(text) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .verticalScroll(rememberScrollState())
            .padding(16.dp),
    ) {
        Text("Server allowlist", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(6.dp))
        Text(
            "Exact hostnames or IP addresses this build is allowed to test. " +
                "One host per line; lines starting with # are ignored.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        InfoCard(
            title = "Authorization gate",
            body = "The combined suite, the idle bots and the ramp test refuse any host that is " +
                "not listed here. The local flood test additionally refuses anything that is " +
                "not this device.",
            accent = MaterialTheme.colorScheme.secondary,
        )

        SectionTitle("Allowlist")
        OutlinedTextField(
            value = text,
            onValueChange = { text = it; saved = false },
            label = { Text("hosts") },
            minLines = 6,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            "${entries.size} host(s) allowed: ${entries.joinToString(", ").ifEmpty { "none" }}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(14.dp))
        Button(
            onClick = {
                viewModel.updateWhitelist(text)
                saved = true
            },
        ) { Text("Save allowlist") }
        if (saved) {
            Spacer(Modifier.height(8.dp))
            Text("Saved.", color = MaterialTheme.colorScheme.primary)
        }
        Spacer(Modifier.height(24.dp))
    }
}
