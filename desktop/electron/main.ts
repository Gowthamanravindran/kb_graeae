import { app, BrowserWindow, dialog, ipcMain, session, shell } from "electron";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { lockDown } from "./security";
import { SidecarError, startSidecar, stopSidecar, type Sidecar } from "./sidecar";

let win: BrowserWindow | null = null;
let engine: Sidecar | null = null;
let engineError: { message: string; log: string; logPath: string } | null = null;

const repoRoot = path.resolve(__dirname, "..", "..");
const uiDir = app.isPackaged ? path.join(process.resourcesPath, "ui") : path.join(__dirname, "..", "dist");
const kbHome = process.env.KB_HOME || path.join(os.homedir(), ".kb");
const logDir = path.join(app.getPath("userData"), "logs");

function engineCommand(): { command: string; args: string[] } {
  const common = ["--port", "0", "--kb-home", kbHome, "--ui-dir", uiDir];
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
    engine = await startSidecar({ command, args, env: process.env, logDir, timeoutMs: Number(process.env.KB_SIDECAR_TIMEOUT_MS) || 30000 });
    const origin = `http://127.0.0.1:${engine.port}`;
    session.defaultSession.webRequest.onBeforeSendHeaders({ urls: [`${origin}/*`] }, (details, cb) => {
      cb({ requestHeaders: { ...details.requestHeaders, "X-KB-Token": engine!.token } });
    });
    engine.proc.on("exit", () => {
      engine = null;
    });
  } catch (e) {
    const err = e as SidecarError;
    engineError = { message: err.message, log: err.logTail ?? "", logPath: err.logPath ?? logDir };
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
  await stopSidecar(engine?.proc ?? null);
  await bootEngine();
  load();
});
ipcMain.handle("kb:open-logs", () => shell.openPath(logDir));
ipcMain.handle("kb:open-external", (_e, url: string) => {
  if (typeof url === "string" && /^https?:\/\//.test(url)) return shell.openExternal(url);
});
ipcMain.handle("kb:open-file", (_e, filePath: string, line: number) => {
  if (typeof filePath !== "string" || !path.isAbsolute(filePath) || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) return;
  if (process.env.KB_EDITOR === "vscode") return shell.openExternal(`vscode://file${filePath}:${Number(line) || 1}`);
  return shell.openPath(filePath);
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
  let quitting = false;
  app.on("before-quit", (event) => {
    if (quitting || !engine) return;
    event.preventDefault();
    quitting = true;
    void stopSidecar(engine.proc).finally(() => app.quit());
  });
}
