import { editorUrl, openCitation } from "./citations";

const c = { project: "sop", branch: "feature/x", repo: "web", file: "src/a.ts", line: 7 };

function deps(res: { status: number; body: unknown }) {
  const fetch = vi.fn(async () => new Response(JSON.stringify(res.body), { status: res.status, headers: { "content-type": "application/json" } }));
  return { fetch: fetch as unknown as typeof globalThis.fetch, openPath: vi.fn(async () => ""), openExternal: vi.fn(async () => {}) };
}

test("open_citation_asks_the_engine_then_opens", async () => {
  const d = deps({ status: 200, body: { path: "/w/src/a.ts", line: 7 } });
  expect(await openCitation({ port: 4000, token: "t" }, c, d)).toEqual({ ok: true });
  expect(d.fetch).toHaveBeenCalledWith(
    "http://127.0.0.1:4000/api/projects/sop/branches/feature/x/citation?repo=web&file=src%2Fa.ts&line=7",
    { headers: { "X-KB-Token": "t" } },
  );
  expect(d.openPath).toHaveBeenCalledWith("/w/src/a.ts");
});

test("open_citation_refuses_paths_the_engine_did_not_give", async () => {
  const d = deps({ status: 404, body: { detail: "file not found in your checkout of web" } });
  expect(await openCitation({ port: 4000, token: "t" }, c, d)).toEqual({ ok: false, error: "file not found in your checkout of web" });
  expect(d.openPath).not.toHaveBeenCalled();
});

test("open_citation_without_engine", async () => {
  const d = deps({ status: 200, body: {} });
  expect((await openCitation(null, c, d)).ok).toBe(false);
});

test("vscode_editor_opens_url", async () => {
  const d = deps({ status: 200, body: { path: "/w/src/a.ts", line: 7 } });
  await openCitation({ port: 4000, token: "t" }, c, { ...d, editor: "vscode" });
  expect(d.openExternal).toHaveBeenCalledWith("vscode://file/w/src/a.ts:7");
});

test("editor_url_handles_windows_paths", () => {
  expect(editorUrl("C:\\w\\a.ts", 3)).toBe("vscode://file/C:/w/a.ts:3");
  expect(editorUrl("/w/a.ts", 3)).toBe("vscode://file/w/a.ts:3");
});

function revealDeps(path: string, platform: string) {
  const d = deps({ status: 200, body: { path, line: 3 } });
  return { ...d, showItemInFolder: vi.fn(), platform };
}

test("script_like_files_are_shown_in_the_folder_not_opened", async () => {
  for (const p of ["/w/scripts/deploy.sh", "/w/run.command", "/w/Tool.app", "/w/Makefile", "/w/x.ps1"]) {
    const d = revealDeps(p, "darwin");
    expect(await openCitation({ port: 4000, token: "t" }, c, d)).toEqual({ ok: true, revealed: true });
    expect(d.openPath).not.toHaveBeenCalled();
    expect(d.showItemInFolder).toHaveBeenCalledWith(p);
  }
});

test("javascript_is_not_opened_on_windows", async () => {
  const win = revealDeps("C:\\w\\src\\a.js", "win32");
  await openCitation({ port: 4000, token: "t" }, c, win);
  expect(win.openPath).not.toHaveBeenCalled();
  const mac = revealDeps("/w/src/a.js", "darwin");
  await openCitation({ port: 4000, token: "t" }, c, mac);
  expect(mac.openPath).toHaveBeenCalledWith("/w/src/a.js");
});

test("source_files_still_open", async () => {
  for (const p of ["/w/src/a.ts", "/w/src/B.tsx", "/w/schema.graphql", "/w/README.md", "/w/kb.yaml"]) {
    const d = revealDeps(p, "win32");
    await openCitation({ port: 4000, token: "t" }, c, d);
    expect(d.openPath).toHaveBeenCalledWith(p);
  }
});
