/**
 * What the renderer is allowed to know about its host.
 *
 * `client.ts` prefers `window.gauntlet.apiBase` over anything decided at build
 * time, because the port is chosen when the app starts. The main process
 * passes it through `additionalArguments`, which is the only channel a preload
 * script can read before the page exists.
 */

import { contextBridge } from "electron";

const FLAG = "--gauntlet-api-base=";

const apiBase =
  process.argv.find((argument) => argument.startsWith(FLAG))?.slice(FLAG.length) ?? "";

contextBridge.exposeInMainWorld("gauntlet", { apiBase });
