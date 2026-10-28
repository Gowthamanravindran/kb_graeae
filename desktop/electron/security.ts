import { shell, type BrowserWindow } from "electron";

/** No popups, no navigation away from the app; http(s) links go to the system browser. */
export function lockDown(win: BrowserWindow, allowedOrigin: () => string | null) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (/^https?:\/\//.test(url) && !url.startsWith("http://127.0.0.1")) void shell.openExternal(url);
    return { action: "deny" };
  });
  win.webContents.on("will-navigate", (event, url) => {
    const origin = allowedOrigin();
    if (!origin || !url.startsWith(origin)) event.preventDefault();
  });
}
