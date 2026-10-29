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
