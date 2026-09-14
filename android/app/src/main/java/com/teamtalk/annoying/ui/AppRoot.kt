package com.teamtalk.annoying.ui

import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Home
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import com.teamtalk.annoying.core.LicenseGate
import com.teamtalk.annoying.ui.screens.AboutScreen
import com.teamtalk.annoying.ui.screens.ConnectionScreen
import com.teamtalk.annoying.ui.screens.HomeScreen
import com.teamtalk.annoying.ui.screens.LogScreen
import com.teamtalk.annoying.ui.screens.ToolScreen
import com.teamtalk.annoying.ui.screens.WhitelistScreen

private enum class Dest(val route: String, val label: String, val icon: ImageVector) {
    Home("home", "Tools", Icons.Filled.Home),
    Log("log", "Log", Icons.Filled.PlayArrow),
    Connection("connection", "Server", Icons.Filled.Settings),
    Allowlist("allowlist", "Allowlist", Icons.Filled.Lock),
    About("about", "About", Icons.Filled.Info),
}

const val ROUTE_TOOL_PREFIX = "tool/"

@Composable
fun AppRoot() {
    val navController = rememberNavController()
    val viewModel: AppViewModel = viewModel()
    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    var licensePromptDismissed by remember { mutableStateOf(false) }

    Scaffold(
        containerColor = MaterialTheme.colorScheme.background,
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface) {
                Dest.values().forEach { destination ->
                    NavigationBarItem(
                        selected = currentRoute == destination.route,
                        onClick = {
                            if (currentRoute != destination.route) {
                                navController.navigate(destination.route) {
                                    launchSingleTop = true
                                    restoreState = true
                                }
                            }
                        },
                        icon = { Icon(destination.icon, contentDescription = destination.label) },
                        label = { Text(destination.label) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = Dest.Home.route,
            modifier = Modifier.padding(padding),
        ) {
            composable(Dest.Home.route) { HomeScreen(viewModel, navController) }
            composable(Dest.Log.route) { LogScreen() }
            composable(Dest.Connection.route) { ConnectionScreen(viewModel) }
            composable(Dest.Allowlist.route) { WhitelistScreen(viewModel) }
            composable(Dest.About.route) { AboutScreen(viewModel) }
            composable("$ROUTE_TOOL_PREFIX{id}") { entry ->
                val toolId = entry.arguments?.getString("id").orEmpty()
                ToolScreen(viewModel, navController, toolId)
            }
        }
    }

    if (!viewModel.licenseAccepted && !licensePromptDismissed) {
        LicenseDialog(
            onAccept = { viewModel.acceptLicense() },
            onDecline = { licensePromptDismissed = true },
        )
    }
}

@Composable
private fun LicenseDialog(onAccept: () -> Unit, onDecline: () -> Unit) {
    val context = LocalContext.current
    val licenseText = remember { LicenseGate.licenseText(context) }
    AlertDialog(
        onDismissRequest = { /* the user must make a choice */ },
        title = { Text("TeamTalk 5 SDK license") },
        text = {
            Text(
                licenseText,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier.verticalScroll(rememberScrollState()),
            )
        },
        confirmButton = {
            TextButton(onClick = onAccept) { Text("I accept") }
        },
        dismissButton = {
            TextButton(onClick = onDecline) { Text("Decline") }
        },
    )
}
