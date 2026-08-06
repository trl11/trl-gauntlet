/**
 * The desktop shell: it starts a backend, waits for it, and shows it.
 *
 * The backend serves the same bundle the browser gets, so the window loads it
 * over http from the port the backend was given rather than from disk. There
 * is one copy of the frontend, inside the wheel, and the renderer is same
 * origin with the API it calls.
 */

import { type ChildProcess, spawn } from "node:child_process";
import { createServer } from "node:net";
import path from "node:path";
import { app, BrowserWindow, shell } from "electron";

/** How long the backend gets to answer `/api/health` before the shell gives up. */
const STARTUP_TIMEOUT_MS = 30_000;

/** How often the shell asks, while it waits. */
const POLL_INTERVAL_MS = 200;

/** Kept so the last lines of stderr can be shown when startup fails. */
const STDERR_LINES_KEPT = 40;

/** The repository root in development; irrelevant once packaged. */
const REPO_ROOT = path.resolve(__dirname, "..", "..");

let backend: ChildProcess | null = null;
let backendErrors: string[] = [];

/**
 * The command that starts the backend, as program and arguments.
 *
 * Packaged, this is the relocatable CPython in `resources/runtime` running
 * `gauntlet` as a module. It is deliberately not the `gauntlet` console script
 * beside it: pip writes an absolute shebang naming the interpreter as it stood
 * when the runtime was built, and that path does not exist on the machine the
 * app is installed on, so exec fails with ENOENT. The interpreter itself is
 * relocatable, which is why it is the one thing invoked by path.
 *
 * `-s` keeps it out of ~/.local/lib/pythonX.Y/site-packages, which it would
 * otherwise read ahead of its own whenever the machine has the same minor
 * version. The bundle carries everything it imports, and a stray copy of one
 * of those on the machine it runs on is not a copy anyone chose.
 *
 * In development it is the checkout's virtualenv, which is what `make run`
 * uses, and whose shebang is correct because nothing moved.
 */
function backendCommand(): { args: string[]; program: string } {
  if (app.isPackaged) {
    return {
      program: path.join(process.resourcesPath, "runtime", "bin", "python3"),
      args: ["-s", "-m", "gauntlet"],
    };
  }
  return { program: path.join(REPO_ROOT, ".venv", "bin", "gauntlet"), args: [] };
}

/**
 * Environment for the backend.
 *
 * Packaged, the suites are read-only beside the runtime and the state lives
 * wherever Electron puts this app's user data. In development nothing is
 * overridden and the working directory is the checkout, so the app reads and
 * writes exactly what `make run` does.
 */
function backendEnvironment(): NodeJS.ProcessEnv {
  if (!app.isPackaged) return process.env;
  return {
    ...process.env,
    GAUNTLET_DATA_DIR: app.getPath("userData"),
    GAUNTLET_SUITE_PATH: path.join(process.resourcesPath, "suites"),
  };
}

/** A port the backend can have, asked of the kernel rather than assumed. */
function freePort(): Promise<number> {
  return new Promise((resolve, reject) => {
    const server = createServer();
    server.on("error", reject);
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      if (typeof address !== "object" || address === null) {
        reject(new Error("the kernel did not name a port"));
        return;
      }
      const { port } = address;
      server.close(() => resolve(port));
    });
  });
}

function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

/**
 * Start the backend on `port`.
 *
 * Detached, so it leads its own process group and a suite it spawned goes with
 * it when the group is signalled. This is the rule `make stop` already follows.
 */
function startBackend(port: number): void {
  backendErrors = [];
  const { args, program } = backendCommand();
  backend = spawn(program, [...args, "serve", "--host", "127.0.0.1", "--port", String(port)], {
    cwd: app.isPackaged ? app.getPath("userData") : REPO_ROOT,
    detached: true,
    env: backendEnvironment(),
    stdio: ["ignore", "inherit", "pipe"],
  });
  backend.stderr?.on("data", (chunk: Buffer) => {
    const text = chunk.toString();
    process.stderr.write(text);
    backendErrors.push(text);
    backendErrors = backendErrors.slice(-STDERR_LINES_KEPT);
  });
  // A spawn that never started reaches this rather than stderr, and it is the
  // one failure with nothing else to show: written out as well as kept, so a
  // headless run says why instead of only the window it has no one to show.
  backend.on("error", (error) => {
    backendErrors.push(String(error));
    process.stderr.write(`${error}\n`);
  });
}

/** Signal the backend's process group, so a suite mid-run goes with it. */
function stopBackend(): void {
  const running = backend;
  backend = null;
  if (running?.pid === undefined) return;
  try {
    process.kill(-running.pid, "SIGTERM");
  } catch {
    // Already gone, which is the outcome this wanted.
  }
}

/** Resolves once the backend answers, rejects when it has had long enough. */
async function waitForBackend(apiBase: string): Promise<void> {
  const deadline = Date.now() + STARTUP_TIMEOUT_MS;
  for (;;) {
    if (backend === null || backend.exitCode !== null) {
      throw new Error(`the backend exited\n\n${backendErrors.join("")}`);
    }
    try {
      const response = await fetch(`${apiBase}/api/health`);
      if (response.ok) return;
    } catch {
      // Not listening yet, which is the ordinary case for the first second.
    }
    if (Date.now() >= deadline) {
      throw new Error(
        `the backend did not answer within ${STARTUP_TIMEOUT_MS / 1000}s` +
          `\n\n${backendErrors.join("")}`
      );
    }
    await delay(POLL_INTERVAL_MS);
  }
}

function escapeHtml(text: string): string {
  return text
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

/**
 * A page rendered from a data URL, shown in place of the bundle.
 *
 * The bundle cannot be shown until the backend serves it, so the window needs
 * something of its own to say while it waits and something to say if the wait
 * ends badly. Colours match `theme.scss`, which this file cannot import.
 */
function messagePage(heading: string, detail: string): string {
  const html = `<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Gauntlet</title><style>
  html { color-scheme: dark; }
  body { background: #030712; color: #f9fafb; margin: 0; height: 100vh;
         display: flex; flex-direction: column; gap: 1rem;
         align-items: center; justify-content: center;
         font-family: system-ui, sans-serif; }
  h1 { font-size: 1.125rem; font-weight: 500; margin: 0; }
  pre { color: #9ca3af; font-size: 0.75rem; max-width: 44rem; max-height: 50vh;
        margin: 0; overflow: auto; white-space: pre-wrap; }
</style></head>
<body><h1>${escapeHtml(heading)}</h1><pre>${escapeHtml(detail)}</pre></body></html>`;
  return `data:text/html;charset=utf-8,${encodeURIComponent(html)}`;
}

/**
 * The window, shown before the backend is up so that starting the app looks
 * like something happening. `backgroundColor` is what paints until the first
 * frame arrives, and white would be a flash of the wrong theme.
 */
function createWindow(apiBase: string): BrowserWindow {
  const window = new BrowserWindow({
    title: "Gauntlet",
    backgroundColor: "#030712",
    width: 1440,
    height: 960,
    minWidth: 900,
    autoHideMenuBar: true,
    webPreferences: {
      additionalArguments: [`--gauntlet-api-base=${apiBase}`],
      contextIsolation: true,
      nodeIntegration: false,
      preload: path.join(__dirname, "preload.js"),
    },
  });
  // A link to anything else is the operator's browser's business.
  window.webContents.setWindowOpenHandler(({ url }) => {
    void shell.openExternal(url);
    return { action: "deny" };
  });
  return window;
}

async function start(): Promise<void> {
  const port = await freePort();
  const apiBase = `http://127.0.0.1:${port}`;
  const window = createWindow(apiBase);
  await window.loadURL(messagePage("Starting Gauntlet", ""));

  startBackend(port);
  try {
    await waitForBackend(apiBase);
  } catch (error) {
    const detail = error instanceof Error ? error.message : String(error);
    await window.loadURL(messagePage("Gauntlet could not start", detail));
    return;
  }
  await window.loadURL(apiBase);
}

app.whenReady().then(start);

// Linux only, so there is no window-less application to keep alive.
app.on("window-all-closed", () => app.quit());

app.on("will-quit", stopBackend);
