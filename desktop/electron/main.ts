import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { openCitation, type CitationRef } from "./citations";
import { defaultEngineEnv } from "./env";
import { revealConfig } from "./reveal";
import { lockDown } from "./security";
import { SidecarError, startSidecar, stopSidecar, type Sidecar } from "./sidecar";

let win: BrowserWindow | null = null;
let engine: Sidecar | null = null;
let engineError: { message: string; log: string; logPath: string; stopped?: boolean } | null = null;
let quitting = false;

const repoRoot = path.resolve(__dirname, "..", "..");
const uiDir = app.isPackaged ? path.join(process.resourcesPath, "ui") : path.join(__dirname, "..", "dist");
const kbHome = process.env.KB_HOME || path.join(os.homedir(), ".kb");
const logDir = path.join(app.getPath("userData"), "logs");

function engineCommand(): { command: string; args: string[] } {
  const common = ["--port", "0", "--kb-home", kbHome, "--ui-dir", uiDir, "--exit-on-stdin-eof"];
  if (process.env.KB_SERVE_CMD) {
    const [command, ...rest] = process.env.KB_SERVE_CMD.split(" ").filter(Boolean);
    return { command, args: [...rest, ...common] };
  }
  if (app.isPackaged) return { command: path.join(process.resourcesPath, "kb-serve", process.platform === "win32" ? "kb-serve.exe" : "kb-serve"), args: common };
  return { command: "uv", args: ["run", "--project", repoRoot, "kb", "serve", ...common] };
}

async function bootEngine(): Promise<void> {
  engineError = null;
  try {
    const { command, args } = engineCommand();
    engine = await startSidecar({ command, args, env: defaultEngineEnv(), logDir, timeoutMs: Number(process.env.KB_SIDECAR_TIMEOUT_MS) || 30000 });
    const origin = `http://127.0.0.1:${engine.port}`;
    const token = engine.token; // captured: `engine` is null once it stops, and this callback must always answer
    session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, cb) => {
      cb({ requestHeaders: { ...details.requestHeaders, "X-KB-Token": token } });
    });
    const started = engine;
    started.proc.on("exit", (code, signal) => {
      if (engine !== started) return; // an older engine finishing after a retry
      engine = null;
      if (quitting) return;
      engineError = {
        message: `the engine stopped unexpectedly (${signal ?? `code ${code}`})`,
        log: tailFile(started.logPath), logPath: started.logPath, stopped: true,
      };
      // Tell the open page instead of navigating it away: it shows the error screen with Retry.
      win?.webContents.send("kb:engine-stopped");
    });
  } catch (e) {
    const err = e as SidecarError;
    engineError = { message: err.message, log: err.logTail ?? "", logPath: err.logPath ?? logDir };
  }
}

function tailFile(file: string, lines = 40): string {
  try {
    return fs.readFileSync(file, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "";
  }
}

function load(): void {
  if (!win) return;
  if (engine) void win.loadURL(`http://127.0.0.1:${engine.port}/`);
  else void win.loadFile(path.join(uiDir, "index.html"), { hash: "engine-error" });
}

function createWindow(): void {
  win = new BrowserWindow({
    width: 1280, height: 840, minWidth: 960, minHeight: 640, title: "KB Desktop", backgroundColor: "#F6F6F4",
    webPreferences: { preload: path.join(__dirname, "preload.js"), contextIsolation: true, sandbox: true, nodeIntegration: false, webSecurity: true },
  });
  lockDown(win, () => (engine ? `http://127.0.0.1:${engine.port}` : null));
  load();
}

ipcMain.handle("kb:engine-status", () => (engine ? { ok: true } : { ok: false, ...engineError }));
ipcMain.handle("kb:retry-engine", async () => {
  const old = engine;
  engine = null;
  await stopSidecar(old?.proc ?? null);
  await bootEngine();
  load();
});
ipcMain.handle("kb:open-logs", () => shell.openPath(logDir));
ipcMain.handle("kb:open-external", (_e, url: string) => {
  if (typeof url === "string" && /^https?:\/\//.test(url)) return shell.openExternal(url);
});
ipcMain.handle("kb:open-citation", (_e, c: CitationRef) =>
  openCitation(engine, c, { fetch: globalThis.fetch, openPath: shell.openPath, openExternal: shell.openExternal,
    showItemInFolder: shell.showItemInFolder, platform: process.platform, editor: process.env.KB_EDITOR }));
ipcMain.handle("kb:reveal-config", (_e, project: string) =>
  revealConfig(engine, String(project), { fetch: globalThis.fetch, showItemInFolder: shell.showItemInFolder }));
ipcMain.handle("kb:save-file", async (_e, defaultName: string, data: Uint8Array) => {
  if (process.env.KB_E2E_SAVE_DIR) { // end-to-end tests only: no native dialog to click
    const p = path.join(process.env.KB_E2E_SAVE_DIR, path.basename(String(defaultName)));
    await fs.promises.writeFile(p, Buffer.from(data));
    return { saved: true, path: p };
  }
  const r = await dialog.showSaveDialog({ defaultPath: path.join(app.getPath("downloads"), path.basename(String(defaultName))),
    filters: [{ name: "CSV", extensions: ["csv"] }] });
  if (r.canceled || !r.filePath) return { saved: false };
  await fs.promises.writeFile(r.filePath, Buffer.from(data));
  return { saved: true, path: r.filePath };
});
ipcMain.handle("kb:pick-folder", async () => {
  const r = await dialog.showOpenDialog({ properties: ["openDirectory"] });
  return r.canceled ? null : r.filePaths[0];
});
ipcMain.handle("kb:pick-file", async (_e, filters: { name: string; extensions: string[] }[]) => {
  const r = await dialog.showOpenDialog({ properties: ["openFile"], filters });
  return r.canceled ? null : r.filePaths[0];
});

if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on("second-instance", () => win?.focus());
  app.whenReady().then(async () => {
    await bootEngine();
    createWindow();
  });
  app.on("window-all-closed", () => app.quit());
  app.on("before-quit", (event) => {
    if (quitting || !engine) {
      quitting = true;
      return;
    }
    event.preventDefault();
    quitting = true;
    void stopSidecar(engine.proc).finally(() => app.quit());
  });
}
