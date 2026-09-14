/**
 * Headless render check: renders the app shell and each screen to a string, so
 * a blank page or a crash on first paint is caught without a browser.
 *
 *   bun scripts/render-check.ts
 */
import "./browser-shim";
import { createElement } from "react";
import type { ReactElement } from "react";
import { renderToString } from "react-dom/server";
import { App } from "../src/app/App";
import { AboutScreen } from "../src/app/ui/screens/AboutScreen";
import { BridgeScreen } from "../src/app/ui/screens/BridgeScreen";
import { ConsoleScreen } from "../src/app/ui/screens/ConsoleScreen";
import { DashboardScreen } from "../src/app/ui/screens/DashboardScreen";
import { SettingsScreen } from "../src/app/ui/screens/SettingsScreen";
import { ToolsScreen } from "../src/app/ui/screens/ToolsScreen";

let failures = 0;

function render(name: string, element: ReactElement, expected: string[]): void {
  try {
    const html = renderToString(element);
    const missing = expected.filter((needle) => !html.includes(needle));
    if (missing.length === 0) {
      console.log(`  PASS  ${name} rendered (${html.length} bytes of markup)`);
    } else {
      failures += 1;
      console.log(`  FAIL  ${name} is missing: ${missing.join(", ")}`);
    }
  } catch (error) {
    failures += 1;
    console.log(`  FAIL  ${name} threw: ${error instanceof Error ? error.message : error}`);
  }
}

console.log("render check");

render("app shell", createElement(App), [
  "Annoying Tools",
  "Dashboard",
  "Tools",
  "Console",
  "Server &amp; allowlist",
  "Bridge &amp; admin",
  "About",
  "Live server",
]);

render("dashboard", createElement(DashboardScreen), [
  "Simulated TeamTalk server",
  "Roster",
  "Server state",
  "/Lobby",
]);

render("tools", createElement(ToolsScreen), [
  "Message sender",
  "Local flood test",
  "allowlist gate",
  "Run",
]);

render("console", createElement(ConsoleScreen), ["Console", "Nothing logged at this level yet"]);

render("settings", createElement(SettingsScreen), [
  "Connection",
  "Allowlist",
  "Install as an app",
  "Run limits in this panel",
]);

render("bridge & admin", createElement(BridgeScreen), [
  "Webby bridge",
  "Administrator",
  "Allowlist file",
  "run_webby.sh",
]);

render("about", createElement(AboutScreen), [
  "What this is",
  "tt_message_spammer.py",
  "Safety gates, kept intact",
  "Honest limitations",
]);

if (failures > 0) {
  console.log(`\n  ${failures} failing render check(s)`);
  process.exit(1);
}
console.log("\n  every screen rendered");
