import { contextBridge, ipcRenderer } from "electron";

// The engine secret is added by the main process to every request; the page never sees it.
contextBridge.exposeInMainWorld("kb", {
  baseUrl: "",
  token: "",
  openExternal: (url: string) => ipcRenderer.invoke("kb:open-external", url),
  openFile: (filePath: string, line?: number) => ipcRenderer.invoke("kb:open-file", filePath, line ?? 1),
  pickFile: (filters?: { name: string; extensions: string[] }[]) => ipcRenderer.invoke("kb:pick-file", filters ?? []),
  engineStatus: () => ipcRenderer.invoke("kb:engine-status"),
  retryEngine: () => ipcRenderer.invoke("kb:retry-engine"),
  openLogs: () => ipcRenderer.invoke("kb:open-logs"),
});
