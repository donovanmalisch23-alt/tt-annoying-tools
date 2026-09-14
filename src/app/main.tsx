import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import { loadOfflineEnabled } from "./core/settings";
import { registerServiceWorker } from "./pwa";
import "./styles.css";

const container = document.getElementById("root");
if (!container) throw new Error("index.html is missing the #root element");

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);

// Production builds are installable offline. In development the worker is only
// registered when the operator explicitly opts in, so a stale shell can never
// mask fresh code.
if (import.meta.env.PROD || loadOfflineEnabled()) registerServiceWorker();
