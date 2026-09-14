package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.teamtalk.annoying.core.Whitelist
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.Badge
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.SectionTitle

/** The exact-host allowlist: the gate every bulk tool checks before it connects. */
@Composable
fun AdminAllowlistSection(viewModel: AppViewModel) {
    var newHost by remember { mutableStateOf("") }
    var message by remember { mutableStateOf<String?>(null) }
    var failed by remember { mutableStateOf(false) }
    var rawMode by remember { mutableStateOf(false) }
    var rawText by remember { mutableStateOf(viewModel.whitelistText) }

    val entries = viewModel.whitelistEntries
    val targetHost = viewModel.config.host

    SectionTitle("Server allowlist")
    Text(
        text = "Exact hostnames or IP addresses this build is allowed to test, one per " +
            "line. The combined suite, the idle bots and the ramp test refuse anything " +
            "that is not listed here.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    Spacer(Modifier.height(10.dp))
    InfoCard(
        title = "Authorization gate",
        body = "Use these tools only on a server you own or administer, and only where the " +
            "participants agreed to the test. The local flood test additionally refuses " +
            "anything that is not this device.",
        accent = MaterialTheme.colorScheme.secondary,
    )

    Spacer(Modifier.height(12.dp))
    OutlinedTextField(
        value = newHost,
        onValueChange = { newHost = it; message = null },
        label = { Text("Hostname or IP") },
        singleLine = true,
        modifier = Modifier.fillMaxWidth(),
    )
    Spacer(Modifier.height(8.dp))
    Button(
        onClick = {
            val error = viewModel.addHost(newHost)
            if (error == null) {
                message = "Added '${Whitelist.normalize(newHost)}'."
                failed = false
                newHost = ""
                rawText = viewModel.whitelistText
            } else {
                message = error
                failed = true
            }
        },
    ) { Text("Add host to allowlist") }

    message?.let {
        Spacer(Modifier.height(8.dp))
        Text(
            text = it,
            style = MaterialTheme.typography.bodyMedium,
            color = if (failed) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.primary,
        )
    }

    Spacer(Modifier.height(16.dp))
    Text(
        text = "${entries.size} host(s) allowed",
        style = MaterialTheme.typography.labelLarge,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )
    Spacer(Modifier.height(6.dp))
    if (entries.isEmpty()) {
        InfoCard(
            title = "No host is allowed yet",
            body = "Every tool that checks the allowlist stays blocked until a host is added.",
            accent = MaterialTheme.colorScheme.secondary,
        )
    } else {
        entries.forEach { host ->
            val isTarget = targetHost.isNotBlank() && Whitelist.normalize(targetHost) == host
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(12.dp),
                border = BorderStroke(1.dp, MaterialTheme.colorScheme.outline.copy(alpha = 0.5f)),
                modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
            ) {
                Row(
                    modifier = Modifier.padding(start = 12.dp, top = 4.dp, bottom = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    Text(
                        text = host,
                        style = MaterialTheme.typography.bodyLarge,
                        fontFamily = FontFamily.Monospace,
                        fontWeight = FontWeight.Medium,
                    )
                    if (isTarget) {
                        Spacer(Modifier.width(8.dp))
                        Badge("current target", MaterialTheme.colorScheme.primary)
                    }
                    Spacer(Modifier.weight(1f))
                    IconButton(onClick = {
                        viewModel.removeHost(host)
                        rawText = viewModel.whitelistText
                        message = "Removed '$host'."
                        failed = false
                    }) {
                        Icon(
                            imageVector = Icons.Filled.Delete,
                            contentDescription = "Remove $host",
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
        }
    }

    Spacer(Modifier.height(12.dp))
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        TextButton(onClick = {
            rawMode = !rawMode
            rawText = viewModel.whitelistText
        }) { Text(if (rawMode) "Hide the text editor" else "Edit as text") }
    }

    if (rawMode) {
        OutlinedTextField(
            value = rawText,
            onValueChange = { rawText = it; message = null },
            label = { Text("allowlist") },
            minLines = 6,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(8.dp))
        Text(
            text = "Lines starting with # are comments and are ignored.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(8.dp))
        Button(
            onClick = {
                viewModel.updateWhitelist(rawText)
                message = "Saved ${Whitelist.parse(rawText).size} host(s)."
                failed = false
            },
        ) { Text("Save allowlist") }
    }
}
