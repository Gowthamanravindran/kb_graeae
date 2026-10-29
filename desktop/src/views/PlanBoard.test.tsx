import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { PlanCase, TestPlanDetail } from "../api/types";
import { useApp } from "../state";
import { fakeApi, renderApp } from "../test-utils";
import { DETAIL } from "./fixtures.testplan";
import { PlanBoard } from "./PlanBoard";

const kase = (o: Partial<PlanCase>): PlanCase => ({
  id: "TC-001", slot_id: "w0", suggested: false, status: "written", scenario_id: "sc1", flow_id: "f1", title: "Create a weekly task that ends on a date",
  priority: "P0", preconditions: ["Signed in as an admin"], steps: ["Open Tasks → Create task", "Save"], test_data: ["Title: Fire exit check"],
  expected: ["Task is saved"], author: "me", created_at: "", stale: false, ...o,
});
const B: TestPlanDetail = {
  ...DETAIL,
  slots: [...DETAIL.slots, { ...DETAIL.slots[0], id: "st0", status: "stale", case_id: "TC-002" }, { ...DETAIL.slots[0], id: "fl0", status: "failed", fail_reason: "Tracing timed out" }],
  scenarios: [{ id: "sc1", flow_id: "f1", type: "happy_path", title: "Changed happy path", description: "" }],
  cases: [kase({}), kase({ id: "TC-002", slot_id: "st0", status: "stale", stale: true, title: "End date before start" })],
  extras: [kase({ id: "TC-009", slot_id: null, suggested: true, status: "suggested", title: "End date on a public holiday" })],
  coverage: { items: { i1: "covered", i2: "skipped", i3: "failed" }, by_priority: { P0: 1 }, by_status: { written: 1 } },
};

function Probe() {
  const { view, planJobId } = useApp();
  return <output data-testid="probe">{view}|{planJobId ?? ""}</output>;
}
const start = (over = {}) => {
  const api = fakeApi({ testplan: vi.fn(async () => B), ...over });
  renderApp(<><PlanBoard /><Probe /></>, api, { project: "sop", branch: "main", view: "testplans", askSeed: null, planId: "cs1", planScreen: "board" });
  return api;
};
afterEach(() => { delete window.kb; });

test("board_groups_and_expands", async () => {
  start();
  const group = (await screen.findByText("Admin sets a task to repeat weekly", { selector: "h2" })).closest("section")!;
  expect(within(group).getByText("Happy path")).toBeInTheDocument();
  const title = within(group).getByText("Create a weekly task that ends on a date");
  await userEvent.click(title);
  const details = title.closest("details")!;
  expect(details).toHaveAttribute("open");
  expect(within(details).getByText("Open Tasks → Create task")).toBeInTheDocument();
  expect(within(details).getByText("Task is saved")).toBeInTheDocument();
  expect(screen.getByText("1 covered")).toBeInTheDocument();
  expect(screen.getByText("1 failed")).toBeInTheDocument();
  expect(within(group).getByText("Tracing timed out")).toBeInTheDocument();
});

test("filter_applies_to_export", async () => {
  const saveFile = vi.fn(async () => ({ saved: true }));
  window.kb = { saveFile } as unknown as Window["kb"];
  const api = start({ exportCsv: vi.fn(async () => new Uint8Array([1, 2])) });
  await screen.findByText("Admin sets a task to repeat weekly", { selector: "h2" });
  await userEvent.selectOptions(screen.getByLabelText("Filter by priority"), "P0");
  await userEvent.click(screen.getByRole("button", { name: "Export CSV" }));
  expect(api.exportCsv).toHaveBeenCalledWith("sop", "cs1", { priority: ["P0"] });
  await waitFor(() => expect(saveFile).toHaveBeenCalledWith("sop-cs1-test-cases.csv", new Uint8Array([1, 2])));
});

test("stale_regenerate_calls_retry", async () => {
  const api = start({ retrySlot: vi.fn(async () => ({ job_id: "jr" })) });
  await userEvent.click(await screen.findByRole("button", { name: "Regenerate" }));
  expect(api.retrySlot).toHaveBeenCalledWith("sop", "cs1", "st0");
  expect(screen.getByTestId("probe")).toHaveTextContent("testplans|jr");
});

test("failed_retry_and_extras", async () => {
  const api = start({ retrySlot: vi.fn(async () => ({ job_id: "jr" })), acceptExtra: vi.fn(async () => ({ ok: true })), dismissExtra: vi.fn(async () => ({ ok: true })) });
  await userEvent.click(await screen.findByRole("button", { name: "Retry" }));
  expect(api.retrySlot).toHaveBeenCalledWith("sop", "cs1", "fl0");
  const tray = screen.getByRole("region", { name: "Suggested extras" });
  await userEvent.click(within(tray).getByRole("button", { name: "Accept" }));
  expect(api.acceptExtra).toHaveBeenCalledWith("sop", "cs1", "TC-009");
});

test("running_job_shows_progress_then_reloads", async () => {
  let done = false;
  const api = fakeApi({
    testplan: vi.fn(async () => B),
    job: vi.fn(async (id: string) => ({ id, kind: "testplan", key: null, state: done ? "done" : "running", step: "Writing cases for X (1/2)", progress: 0.5, result: null, error: null, created_at: "" })),
  });
  renderApp(<PlanBoard />, api, { project: "sop", branch: "main", view: "testplans", askSeed: null, planId: "cs1", planScreen: "board", planJobId: "j1" });
  expect(await screen.findByText("Writing cases for X (1/2)")).toBeInTheDocument();
  done = true;
  await waitFor(() => expect(api.testplan).toHaveBeenCalledTimes(2), { timeout: 3000 });
});
