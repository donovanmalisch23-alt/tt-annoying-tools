package com.teamtalk.annoying.ui.screens

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import com.teamtalk.annoying.tools.parseLooseInt
import com.teamtalk.annoying.ui.AppViewModel
import com.teamtalk.annoying.ui.components.InfoCard
import com.teamtalk.annoying.ui.components.LabeledTextField
import com.teamtalk.annoying.ui.components.SectionTitle
import com.teamtalk.annoying.ui.components.ToggleRow

/** The target server, its account and the SDK registration, as a panel section. */
@Composable
fun AdminServerSection(viewModel: AppViewModel) {
    val base = viewModel.config
    var host by remember(base) { mutableStateOf(base.host) }
    var tcpPort by remember(base) { mutableStateOf(base.tcpPort.toString()) }
    var udpPort by remember(base) { mutableStateOf(base.udpPort.toString()) }
    var username by remember(base) { mutableStateOf(base.username) }
    var password by remember(base) { mutableStateOf(base.password) }
    var nickname by remember(base) { mutableStateOf(base.nickname) }
    var clientName by remember(base) { mutableStateOf(base.clientName) }
    var encrypted by remember(base) { mutableStateOf(base.encrypted) }
    var channelIdText by remember(base) { mutableStateOf(base.channelId?.toString() ?: "") }
    var channelPath by remember(base) { mutableStateOf(base.channelPath ?: "") }
    var channelPassword by remember(base) { mutableStateOf(base.channelPassword) }
    var timeout by remember(base) { mutableStateOf(base.commandTimeoutSec.toString()) }
    var reconnectDelay by remember(base) { mutableStateOf(base.reconnectDelaySec.toString()) }
    var kickResistance by remember(base) { mutableStateOf(base.kickResistance) }
    var licenseName by remember(base) { mutableStateOf(base.licenseName ?: "") }
    var licenseKey by remember(base) { mutableStateOf(base.licenseKey) }
    var saved by remember { mutableStateOf(false) }

    SectionTitle("Target server")
    Text(
        text = "Connection settings shared by every tool — the Android equivalent of " +
            "teamtalk.env.",
        style = MaterialTheme.typography.bodySmall,
        color = MaterialTheme.colorScheme.onSurfaceVariant,
    )

    Spacer(Modifier.height(10.dp))
    LabeledTextField(
        "Host", host, { host = it; saved = false },
        "Hostname or IP of the TeamTalk server. It must also be on the allowlist.",
    )
    LabeledTextField("TCP port", tcpPort, { tcpPort = it; saved = false }, keyboardType = KeyboardType.Number)
    LabeledTextField("UDP port", udpPort, { udpPort = it; saved = false }, keyboardType = KeyboardType.Number)
    ToggleRow("Encrypted connection", encrypted, { encrypted = it; saved = false })

    SectionTitle("Account")
    LabeledTextField(
        "Username", username, { username = it; saved = false },
        "Leave blank to log in anonymously.",
    )
    LabeledTextField(
        "Password", password, { password = it; saved = false },
        visualTransformation = PasswordVisualTransformation(),
    )
    LabeledTextField("Nickname", nickname, { nickname = it; saved = false })
    LabeledTextField("Client name", clientName, { clientName = it; saved = false })

    SectionTitle("Default channel")
    LabeledTextField(
        "Channel ID", channelIdText, { channelIdText = it; saved = false },
        "Numeric ID; takes precedence over the channel path.",
        keyboardType = KeyboardType.Number,
    )
    LabeledTextField(
        "Channel path", channelPath, { channelPath = it; saved = false },
        "For example /Lobby/Games. Left blank means the root channel.",
    )
    LabeledTextField(
        "Channel password", channelPassword, { channelPassword = it; saved = false },
        visualTransformation = PasswordVisualTransformation(),
    )

    SectionTitle("Behaviour")
    LabeledTextField(
        "Command timeout (s)", timeout, { timeout = it; saved = false },
        keyboardType = KeyboardType.Decimal,
    )
    LabeledTextField(
        "Reconnect delay (s)", reconnectDelay, { reconnectDelay = it; saved = false },
        keyboardType = KeyboardType.Decimal,
    )
    ToggleRow(
        "Kick resistance", kickResistance, { kickResistance = it; saved = false },
        "Rebuild the connection and resume after a kick or disconnect.",
    )

    SectionTitle("SDK registration (optional)")
    InfoCard(
        title = "Only needed for a purchased license",
        body = "Without a registration name and key the bundled SDK runs in trial mode. " +
            "These values are stored on the device only.",
    )
    LabeledTextField("License name", licenseName, { licenseName = it; saved = false })
    LabeledTextField(
        "License key", licenseKey, { licenseKey = it; saved = false },
        visualTransformation = PasswordVisualTransformation(),
    )

    Spacer(Modifier.height(16.dp))
    Button(
        onClick = {
            val updated = base.copy(
                host = host.trim(),
                tcpPort = parseLooseInt(tcpPort) ?: base.tcpPort,
                udpPort = parseLooseInt(udpPort) ?: base.udpPort,
                username = username,
                password = password,
                nickname = nickname.ifBlank { base.nickname },
                clientName = clientName.ifBlank { base.clientName },
                encrypted = encrypted,
                channelId = parseLooseInt(channelIdText)?.takeIf { it >= 0 },
                channelPath = channelPath.trim().takeIf { it.isNotEmpty() },
                channelPassword = channelPassword,
                commandTimeoutSec = timeout.toDoubleOrNull()?.takeIf { it > 0 }
                    ?: base.commandTimeoutSec,
                reconnectDelaySec = reconnectDelay.toDoubleOrNull()?.takeIf { it >= 0 }
                    ?: base.reconnectDelaySec,
                kickResistance = kickResistance,
                licenseName = licenseName.trim().takeIf { it.isNotEmpty() },
                licenseKey = licenseKey,
            )
            viewModel.updateConfig(updated)
            saved = true
        },
        modifier = Modifier.fillMaxWidth(),
    ) { Text("Save target settings") }
    if (saved) {
        Spacer(Modifier.height(8.dp))
        Text("Saved.", color = MaterialTheme.colorScheme.primary)
    }
}
