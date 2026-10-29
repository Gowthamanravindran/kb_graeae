import { isAppUrl } from "./security";

vi.mock("electron", () => ({ shell: { openExternal: vi.fn() } }));

test("is_app_url_compares_whole_origins", () => {
  expect(isAppUrl("http://127.0.0.1:40001/", "http://127.0.0.1:4000")).toBe(false);
  expect(isAppUrl("http://127.0.0.1:4000/x", "http://127.0.0.1:4000")).toBe(true);
  expect(isAppUrl("not a url", "http://127.0.0.1:4000")).toBe(false);
  expect(isAppUrl("http://127.0.0.1:4000/", null)).toBe(false);
});
