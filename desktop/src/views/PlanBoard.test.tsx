import { cleanup, screen, waitFor, within } from "@testing-library/react";
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

// ---------------------------------------------------------------- stage 5b: working as a team

import { ApiError } from "../api/client";

const PEOPLE = (role = "maintainer") => ({
  me: "maint@x", role, trace_locks: [] as { flow_id: string; side: string; holder: string; until: string }[],
  members: [{ email: "maint@x", name: "maint", role: "maintainer" }, { email: "ana@x", name: "ana", role: "contributor" }],
  progress: [{ email: "maint@x", written: 3, reserved: 1 }, { email: "ana@x", written: 2, reserved: 0 }],
});
const SHARED: TestPlanDetail = {
  ...B, shared: true, pending: 0,
  slots: [...B.slots, { ...DETAIL.slots[0], id: "rs0", status: "reserved", reserved_by: "ana@x", reserved_until: "2026-10-31T09:30:00Z", assigned_to: "ana@x" }],
};
const team = (over = {}, role = "maintainer") => start({ testplan: vi.fn(async () => SHARED), testplanPeople: vi.fn(async () => PEOPLE(role)), ...over });

test("share_button_shares_and_shows_badge", async () => {
  const api = start({ shareTestplan: vi.fn(async () => ({ job_id: "jS" })) });
  await userEvent.click(await screen.findByRole("button", { name: "Share with team" }));
  expect(api.shareTestplan).toHaveBeenCalledWith("sop", "cs1");
  await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("testplans|jS"));
});

test("shared_plan_shows_badge_not_share_button", async () => {
  team();
  expect(await screen.findByText("Shared with the team")).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Share with team" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Sync" })).toBeInTheDocument();
});

test("people_strip_shows_written_and_reserved_per_person", async () => {
  team();
  const strip = await screen.findByRole("region", { name: "Team progress" });
  expect(within(strip).getByText(/You/)).toBeInTheDocument();
  expect(within(strip).getByText("3 written · 1 reserved")).toBeInTheDocument();
  expect(within(strip).getByText("ana@x")).toBeInTheDocument();
  expect(within(strip).getByText("2 written")).toBeInTheDocument();
});

test("lead_can_assign_a_flow_and_release_a_slot", async () => {
  const api = team({ assignTestplan: vi.fn(async () => ({ assigned: ["w0"] })), releaseTestplan: vi.fn(async () => ({ released: ["rs0"] })) });
  const assign = await screen.findByLabelText("Assign Admin sets a task to repeat weekly");
  await userEvent.selectOptions(assign, "ana@x");
  expect(api.assignTestplan).toHaveBeenCalledWith("sop", "cs1", { flow_ids: ["f1"], email: "ana@x" });
  expect(screen.getByText(/Reserved by ana@x until/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Release" }));
  expect(api.releaseTestplan).toHaveBeenCalledWith("sop", "cs1", { slot_ids: ["rs0"] });
});

test("contributor_sees_no_lead_controls", async () => {
  team({}, "contributor");
  await screen.findByRole("region", { name: "Team progress" });
  expect(screen.queryByLabelText(/^Assign /)).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Release" })).not.toBeInTheDocument();
  expect(screen.getByText(/Reserved by ana@x until/)).toBeInTheDocument();
});

test("reserve_and_generate_on_shared_plan_with_offline_warning", async () => {
  team({ testplanPeople: vi.fn(async () => { throw new ApiError(503, "registry unreachable"); }) });
  await userEvent.click(await screen.findByRole("button", { name: "Generate more" }));
  expect(screen.getByRole("button", { name: "Reserve & generate" })).toBeInTheDocument();
  expect(screen.getByText("Offline: these cases are not reserved and may duplicate a teammate's")).toBeInTheDocument();
});

test("run_result_mentions_teammate_conflicts", async () => {
  const api = fakeApi({
    testplan: vi.fn(async () => SHARED), testplanPeople: vi.fn(async () => PEOPLE()),
    job: vi.fn(async (id: string) => ({ id, kind: "testplan", key: null, state: "done", step: "", progress: 1, created_at: "", error: null,
      result: { written: 2, failed: 0, extras: 0, extras_from_conflicts: 1, warnings: [] } })),
  });
  renderApp(<PlanBoard />, api, { project: "sop", branch: "main", view: "testplans", askSeed: null, planId: "cs1", planScreen: "board", planJobId: "jR" });
  expect(await screen.findByText("1 case became a suggested extra because a teammate wrote it first.")).toBeInTheDocument();
});

test("own_reservation_left_by_a_crash_can_be_released", async () => {
  const mine: TestPlanDetail = { ...SHARED, slots: [...B.slots, { ...DETAIL.slots[0], id: "my0", status: "reserved", reserved_by: "maint@x", reserved_until: "2026-10-31T09:30:00Z" }] };
  const api = team({ testplan: vi.fn(async () => mine), releaseTestplan: vi.fn(async () => ({ released: ["my0"] })) }, "contributor");
  expect(await screen.findByText(/Reserved by you until/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Release" }));
  expect(api.releaseTestplan).toHaveBeenCalledWith("sop", "cs1", { slot_ids: ["my0"] });
});

test("viewer_sees_no_share_or_generate", async () => {
  start({ testplanPeople: vi.fn(async () => PEOPLE("viewer")) });
  expect(await screen.findByRole("button", { name: "Export CSV" })).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Share with team" })).not.toBeInTheDocument());
  expect(screen.queryByRole("button", { name: "Generate more" })).not.toBeInTheDocument();
});

test("shared_viewer_cannot_reserve_and_contributor_cannot_assign", async () => {
  team({}, "viewer");
  expect(await screen.findByText("Shared with the team")).toBeInTheDocument();
  await waitFor(() => expect(screen.queryByRole("button", { name: "Generate more" })).not.toBeInTheDocument());
  cleanup();
  team({}, "contributor");
  expect(await screen.findByRole("button", { name: "Generate more" })).toBeInTheDocument();
  expect(screen.queryByLabelText(/^Assign /)).not.toBeInTheDocument();
});

function PlanProbe() {
  const { planId, planScreen } = useApp();
  return <output data-testid="plan-probe">{planId ?? "none"}|{planScreen}</output>;
}

test("a_plan_that_no_longer_exists_falls_back_to_the_plans_list", async () => {
  const api = fakeApi({ testplan: vi.fn(async () => { throw new ApiError(404, "no test plan cs1"); }) });
  renderApp(<><PlanBoard /><PlanProbe /></>, api, { project: "sop", branch: "main", view: "testplans", askSeed: null, planId: "cs1", planScreen: "board" });
  await waitFor(() => expect(screen.getByTestId("plan-probe")).toHaveTextContent("none|list"));
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
});
