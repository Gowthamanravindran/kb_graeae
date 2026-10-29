import { revealConfig } from "./reveal";

test("reveal_config_uses_engine_path", async () => {
  const fetch = vi.fn(async () => new Response(JSON.stringify([{ name: "sop", config_path: "/x/sop.kb.yaml" }]), { status: 200 }));
  const show = vi.fn();
  expect(await revealConfig({ port: 4000, token: "t" }, "sop", { fetch: fetch as unknown as typeof globalThis.fetch, showItemInFolder: show })).toEqual({ ok: true });
  expect(fetch).toHaveBeenCalledWith("http://127.0.0.1:4000/api/projects", { headers: { "X-KB-Token": "t" } });
  expect(show).toHaveBeenCalledWith("/x/sop.kb.yaml");
});

test("reveal_config_unknown_project", async () => {
  const fetch = vi.fn(async () => new Response("[]", { status: 200 }));
  const show = vi.fn();
  expect((await revealConfig({ port: 4000, token: "t" }, "nope", { fetch: fetch as unknown as typeof globalThis.fetch, showItemInFolder: show })).ok).toBe(false);
  expect(show).not.toHaveBeenCalled();
});
