import { act, render } from "@testing-library/react";
import { AppProvider, useApp } from "./state";
import { fakeApi } from "./test-utils";

test("changing branch or project drops the previous Ask seed", () => {
  let ctx!: ReturnType<typeof useApp>;
  const Grab = () => { ctx = useApp(); return null; };
  render(<AppProvider api={fakeApi()} initial={{ project: "sop", branch: "main", view: "ask", askSeed: { qaId: "q1" } }}><Grab /></AppProvider>);
  act(() => ctx.go("map", { branch: "release/2" }));
  expect(ctx.askSeed).toBeNull();
  act(() => ctx.set({ askSeed: { jobId: "j" } }));
  act(() => ctx.set({ branch: "release/2" })); // same branch: kept
  expect(ctx.askSeed).toEqual({ jobId: "j" });
  act(() => ctx.set({ project: "other" }));
  expect(ctx.askSeed).toBeNull();
  act(() => ctx.go("ask", { branch: "x", askSeed: { question: "q" } })); // explicit seed with the switch wins
  expect(ctx.askSeed).toEqual({ question: "q" });
});

test("focus_rechecks_status", async () => {
  const api = fakeApi();
  render(<AppProvider api={api}><span /></AppProvider>);
  await act(async () => { window.dispatchEvent(new Event("focus")); });
  expect(api.status).toHaveBeenCalledWith(true);
});
