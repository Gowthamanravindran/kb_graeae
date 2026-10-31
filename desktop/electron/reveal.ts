/** Show a project's kb.yaml in Finder/Explorer. The path comes from the engine, never from the page. */
export async function revealConfig(engine: { port: number; token: string } | null, project: string,
  d: { fetch: typeof globalThis.fetch; showItemInFolder(p: string): void }): Promise<{ ok: boolean; error?: string }> {
  if (!engine) return { ok: false, error: "The Graeae engine is not running." };
  try {
    const res = await d.fetch(`http://127.0.0.1:${engine.port}/api/projects`, { headers: { "X-KB-Token": engine.token } });
    const rows = (await res.json()) as { name: string; config_path: string }[];
    const row = rows.find((r) => r.name === project);
    if (!row) return { ok: false, error: `No project named ${project}.` };
    d.showItemInFolder(row.config_path);
    return { ok: true };
  } catch (e) {
    return { ok: false, error: `Could not reach the Graeae engine: ${(e as Error).message}` };
  }
}
