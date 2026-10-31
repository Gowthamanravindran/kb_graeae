import { act, renderHook } from "@testing-library/react";
import { ApiError, createClient } from "./client";
import { useJob } from "./useJob";

function mockFetch(handler: (url: string, init: RequestInit) => { status?: number; body: unknown }) {
  const calls: { url: string; init: RequestInit }[] = [];
  globalThis.fetch = vi.fn(async (url: string, init: RequestInit) => {
    calls.push({ url, init });
    const { status = 200, body } = handler(url, init);
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }) as unknown as typeof fetch;
  return calls;
}

test("client sends the token and parses errors", async () => {
  const calls = mockFetch((url) => (url.endsWith("/api/status") ? { body: { version: "0.1.0" } } : { status: 400, body: { detail: "unknown project: x" } }));
  const api = createClient({ baseUrl: "http://127.0.0.1:5", token: "secret" });
  expect((await api.status()).version).toBe("0.1.0");
  expect((calls[0].init.headers as Record<string, string>)["X-KB-Token"]).toBe("secret");
  await expect(api.branches("x")).rejects.toEqual(new ApiError(400, "unknown project: x"));
});

test("client encodes branch names with slashes as path segments", async () => {
  const calls = mockFetch(() => ({ body: { job_id: "j1" } }));
  const api = createClient({ baseUrl: "http://127.0.0.1:5", token: "t" });
  await api.ask("sop", "feature/x", { question: "How?" });
  expect(calls[0].url).toBe("http://127.0.0.1:5/api/projects/sop/branches/feature/x/ask");
  expect(JSON.parse(calls[0].init.body as string)).toEqual({ question: "How?", from_qa: null, no_reuse: false });
});

test("useJob polls until done and stops after unmount", async () => {
  vi.useFakeTimers();
  let n = 0;
  mockFetch(() => ({ body: { id: "j1", state: ++n >= 3 ? "done" : "running", step: `s${n}`, progress: 0, result: n >= 3 ? { ok: 1 } : null, error: null } }));
  const api = createClient({ baseUrl: "http://x", token: "t" });
  const { result, unmount } = renderHook(() => useJob(api, "j1", 100));
  for (let i = 0; i < 5; i++) await act(async () => { await vi.advanceTimersByTimeAsync(100); });
  expect(result.current.done).toBe(true);
  expect(result.current.job?.result).toEqual({ ok: 1 });
  const before = (globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length;
  unmount();
  await act(async () => { await vi.advanceTimersByTimeAsync(500); });
  expect((globalThis.fetch as unknown as { mock: { calls: unknown[] } }).mock.calls.length).toBe(before);
  vi.useRealTimers();
});

test("useJob gives up when the engine stops answering", async () => {
  const { renderHook, waitFor } = await import("@testing-library/react");
  const { useJob } = await import("./useJob");
  const api = { job: vi.fn(async () => { throw new TypeError("Failed to fetch"); }) } as unknown as Parameters<typeof useJob>[0];
  const { result } = renderHook(() => useJob(api, "j1", 5));
  await waitFor(() => expect(result.current.failed).toBe(true));
  expect(result.current.job?.error).toMatch(/Lost contact with the Graeae engine/);
});
