/** Citations are opened only via the engine's /citation check, so the page can never ask main to open
 * an arbitrary file: main gets the real path from the engine, not from the renderer. */
export interface CitationRef { project: string; branch: string; repo: string; file: string; line: number }
export interface OpenDeps {
  fetch: typeof globalThis.fetch;
  openPath(p: string): Promise<string>;
  openExternal(u: string): Promise<void>;
  editor?: string;
}

const seg = (s: string) => s.split("/").map(encodeURIComponent).join("/");

export function editorUrl(filePath: string, line: number): string {
  const p = filePath.replace(/\\/g, "/").replace(/^\/+/, "");
  return `vscode://file/${p}:${line}`;
}

export async function openCitation(engine: { port: number; token: string } | null, c: CitationRef, d: OpenDeps): Promise<{ ok: boolean; error?: string }> {
  if (!engine) return { ok: false, error: "The KB engine is not running." };
  const q = new URLSearchParams({ repo: c.repo, file: c.file, line: String(c.line || 1) });
  const url = `http://127.0.0.1:${engine.port}/api/projects/${encodeURIComponent(c.project)}/branches/${seg(c.branch)}/citation?${q}`;
  let res: Response;
  try {
    res = await d.fetch(url, { headers: { "X-KB-Token": engine.token } });
  } catch (e) {
    return { ok: false, error: `Could not reach the KB engine: ${(e as Error).message}` };
  }
  const body = (await res.json().catch(() => ({}))) as { path?: string; line?: number; detail?: string };
  if (!res.ok || typeof body.path !== "string") return { ok: false, error: body.detail ?? `The engine refused (${res.status}).` };
  if (d.editor === "vscode") await d.openExternal(editorUrl(body.path, body.line ?? 1));
  else await d.openPath(body.path);
  return { ok: true };
}
