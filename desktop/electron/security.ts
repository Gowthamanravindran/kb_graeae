import { shell, type BrowserWindow } from "electron";

/** True only when `url` is on exactly the app's origin (scheme, host and port). */
export function isAppUrl(url: string, origin: string | null): boolean {
  if (!origin) return false;
  try {
    return new URL(url).origin === origin;
  } catch {
    return false;
  }
}

function isExternalHttp(url: string): boolean {
  try {
    const u = new URL(url);
    return u.protocol === "http:" || u.protocol === "https:";
  } catch {
    return false;
  }
}

/** No popups, no navigation away from the app; external http(s) links go to the system browser. */
export function lockDown(win: BrowserWindow, allowedOrigin: () => string | null) {
  win.webContents.setWindowOpenHandler(({ url }) => {
    if (isExternalHttp(url) && !isAppUrl(url, allowedOrigin()) && new URL(url).hostname !== "127.0.0.1" && new URL(url).hostname !== "localhost")
      void shell.openExternal(url);
    return { action: "deny" };
  });
  const guard = (event: { preventDefault(): void }, url: string) => {
    if (!isAppUrl(url, allowedOrigin())) event.preventDefault();
  };
  win.webContents.on("will-navigate", guard);
  win.webContents.on("will-redirect", guard);
}
