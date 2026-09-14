import { SimServer } from "./server";

/**
 * The one simulated TeamTalk server this tab runs against. Every tool session,
 * the roster panel and the load model all read from this instance, so what you
 * see on the dashboard is exactly what the tools are acting on.
 */
export const server = new SimServer();
