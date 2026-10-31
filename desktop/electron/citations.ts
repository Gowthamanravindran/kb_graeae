/** Citations are opened only via the engine's /citation check, so the page can never ask main to open
 * an arbitrary file: main gets the real path from the engine, not from the renderer. */
export interface CitationRef { project: string; branch: string; repo: string; file: string; line: number }
export interface OpenDeps {
  fetch: typeof globalThis.fetch;
  openPath(p: string): Promise<string>;
  openExternal(u: string): Promise<void>;
  showItemInFolder?(p: string): void;
  platform?: string;
  editor?: string;
}

/** Opened with the OS default app only when that app is an editor or viewer. Anything else — a shell
 * script, `.command`, an app bundle, a file with no extension — could run when "opened", so it is shown in
 * its folder instead. On Windows the default action for `.js` runs it (Windows Script Host). */
const SAFE = new Set(["ts", "tsx", "mts", "cts", "jsx", "json", "md", "markdown", "txt", "yaml", "yml", "graphql", "gql",
  "css", "scss", "less", "sql", "prisma", "go", "java", "kt", "rs", "vue", "svelte", "xml", "csv", "toml"]);
// .py and .rb are left out: their default app can be the interpreter (Windows `py` launcher, macOS Python Launcher)
const SAFE_EXCEPT_WINDOWS = new Set(["js", "mjs", "cjs"]);

export function safeToOpen(filePath: string, platform: string): boolean {
  const base = filePath.split(/[\\/]/).pop() ?? "";
  const dot = base.lastIndexOf(".");
  if (dot <= 0) return false;
  const ext = base.slice(dot + 1).toLowerCase();
  return SAFE.has(ext) || (platform !== "win32" && SAFE_EXCEPT_WINDOWS.has(ext));
}

const seg = (s: string) => s.split("/").map(encodeURIComponent).join("/");

export function editorUrl(filePath: string, line: number): string {
  const p = filePath.replace(/\\/g, "/").replace(/^\/+/, "");
  return `vscode://file/${p}:${line}`;
}

export async function openCitation(engine: { port: number; token: string } | null, c: CitationRef, d: OpenDeps): Promise<{ ok: boolean; error?: string; revealed?: boolean }> {
  if (!engine) return { ok: false, error: "The Graeae engine is not running." };
  const q = new URLSearchParams({ repo: c.repo, file: c.file, line: String(c.line || 1) });
  const url = `http://127.0.0.1:${engine.port}/api/projects/${encodeURIComponent(c.project)}/branches/${seg(c.branch)}/citation?${q}`;
  let res: Response;
  try {
    res = await d.fetch(url, { headers: { "X-KB-Token": engine.token } });
  } catch (e) {
    return { ok: false, error: `Could not reach the Graeae engine: ${(e as Error).message}` };
  }
  const body = (await res.json().catch(() => ({}))) as { path?: string; line?: number; detail?: string };
  if (!res.ok || typeof body.path !== "string") return { ok: false, error: body.detail ?? `The engine refused (${res.status}).` };
  if (d.editor === "vscode") await d.openExternal(editorUrl(body.path, body.line ?? 1));
  else if (safeToOpen(body.path, d.platform ?? process.platform)) await d.openPath(body.path);
  else {
    d.showItemInFolder?.(body.path);
    return { ok: true, revealed: true };
  }
  return { ok: true };
}
