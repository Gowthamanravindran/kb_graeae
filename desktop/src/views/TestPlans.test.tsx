import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { BranchRow, PlanSummary } from "../api/types";
import { useApp } from "../state";
import { fakeApi, renderApp } from "../test-utils";
import { TestPlans } from "./TestPlans";

const plan = (o: Partial<PlanSummary>): PlanSummary => ({
  id: "pr-1", kind: "pr", title: "Tasks: weekly repeat with end date", label: "production",
  base: { "sop-web": "master", "sop-backend": "develop" }, head: { "sop-web": "feature/x", "sop-backend": "feature/x" },
  counts_by_status: { written: 30, planned: 85 }, counts_by_priority: { P0: 34 }, cases_written: 30, updated_at: null, has_impact: true, ...o,
});
const local = { version: 1, status: "complete", commits: {}, author: "me", created_at: null, modules: 3, qa_total: 0, qa_published: 0, synced: false, stats: {} };
const PROD: BranchRow = { branch: "production", local: { ...local, branches: { "sop-web": "master", "sop-backend": "develop" } }, remote: null, state: "local-only", job_id: null };

function Probe() {
  const { view, planId, planScreen } = useApp();
  return <output data-testid="probe">{view}|{planId ?? ""}|{planScreen ?? ""}</output>;
}

function api(over = {}) {
  return fakeApi({
    testplans: vi.fn(async () => [plan({}), plan({ id: "sc-1", kind: "scenario", title: "Admin schedules a repeating task", head: {} })]),
    branches: vi.fn(async () => ({ rows: [PROD], registry_error: null })),
    ...over,
  });
}

test("tabs_switch_lists", async () => {
  renderApp(<TestPlans />, api());
  expect(await screen.findByRole("tab", { name: /Test Plan for PR/ })).toHaveAttribute("aria-selected", "true");
  expect(await screen.findByText("Tasks: weekly repeat with end date")).toBeInTheDocument();
  expect(screen.queryByText("Admin schedules a repeating task")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: /Test Scenarios in a Branch/ }));
  expect(await screen.findByText("Admin schedules a repeating task")).toBeInTheDocument();
  expect(screen.queryByText("Tasks: weekly repeat with end date")).not.toBeInTheDocument();
});

test("pr_form_sends_branch_sets", async () => {
  const a = api({ newPrPlan: vi.fn(async () => ({ job_id: "j1" })) });
  renderApp(<><TestPlans /><Probe /></>, a);
  await userEvent.type(await screen.findByLabelText("sop-web base branch"), "master");
  await userEvent.type(screen.getByLabelText("sop-backend base branch"), "develop");
  await userEvent.type(screen.getByLabelText("sop-web PR branch"), "feature/x");
  expect(await screen.findByText(/Base analysis found: production/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Find what changed" }));
  expect(a.newPrPlan).toHaveBeenCalledWith("sop", { base: { "sop-web": "master", "sop-backend": "develop" },
    head: { "sop-web": "feature/x", "sop-backend": null }, pr_links: {} });
});

test("pr_job_done_opens_impact", async () => {
  const a = api({
    newPrPlan: vi.fn(async () => ({ job_id: "j1" })),
    job: vi.fn(async (id: string) => ({ id, kind: "testplan", key: null, state: "done", step: "", progress: 1, result: { plan_id: "pr-9" }, error: null, created_at: "" })),
  });
  renderApp(<><TestPlans /><Probe /></>, a);
  await userEvent.type(await screen.findByLabelText("sop-web base branch"), "master");
  await userEvent.type(screen.getByLabelText("sop-backend base branch"), "develop");
  await userEvent.type(screen.getByLabelText("sop-web PR branch"), "feature/x");
  await userEvent.click(screen.getByRole("button", { name: "Find what changed" }));
  await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("testplans|pr-9|impact"));
});

test("missing_base_analysis_offers_analyse", async () => {
  const a = api({ analyze: vi.fn(async () => ({ job_id: "ja" })) });
  renderApp(<TestPlans />, a);
  await userEvent.type(await screen.findByLabelText("sop-web base branch"), "main");
  await userEvent.type(screen.getByLabelText("sop-backend base branch"), "main");
  expect(await screen.findByText(/Analyse main first/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Find what changed" })).toBeDisabled();
  await userEvent.click(screen.getByRole("button", { name: "Analyse" }));
  expect(a.analyze).toHaveBeenCalledWith("sop", "main", false);
});

test("scenario_form_starts_plan", async () => {
  const a = api({ newScenarioPlan: vi.fn(async () => ({ job_id: "j2" })) });
  renderApp(<TestPlans />, a);
  await userEvent.click(await screen.findByRole("tab", { name: /Test Scenarios in a Branch/ }));
  const form = screen.getByRole("form", { name: "New scenario test plan" });
  await userEvent.type(within(form).getByLabelText("Scenario"), "Admin schedules a weekly task");
  await userEvent.click(within(form).getByRole("button", { name: "Plan test cases" }));
  expect(a.newScenarioPlan).toHaveBeenCalledWith("sop", { label: "production", text: "Admin schedules a weekly task" });
});

test("open_plan_routes_to_board_or_impact", async () => {
  renderApp(<><TestPlans /><Probe /></>, api({ testplans: vi.fn(async () => [plan({}), plan({ id: "pr-2", title: "Audit v3", cases_written: 0 })]) }));
  await userEvent.click(await screen.findByRole("button", { name: "Choose cases" }));
  expect(screen.getByTestId("probe")).toHaveTextContent("testplans|pr-2|impact");
  await userEvent.click(screen.getByRole("button", { name: "Open board" }));
  expect(screen.getByTestId("probe")).toHaveTextContent("testplans|pr-1|board");
});

test("pr_link_fills_base_and_branch_from_github", async () => {
  const a = api({ prInfo: vi.fn(async () => ({ title: "Repeat end", base_ref: "develop", head_ref: "pull/482/head" })) });
  renderApp(<TestPlans />, a);
  const base = await screen.findByLabelText("sop-backend base branch");
  await userEvent.clear(base);
  await userEvent.type(base, "master"); // as if kb.yaml's default had been pre-filled
  const link = screen.getByLabelText("sop-backend PR link");
  await userEvent.type(link, "https://github.com/acme/sop-backend/pull/482");
  await userEvent.tab();
  await waitFor(() => expect(screen.getByLabelText("sop-backend PR branch")).toHaveValue("pull/482/head"));
  expect(a.prInfo).toHaveBeenCalledWith("https://github.com/acme/sop-backend/pull/482");
  expect(screen.getByLabelText("sop-backend base branch")).toHaveValue("develop");
  expect(screen.getByText(/Repeat end/)).toBeInTheDocument();
});

test("pr_link_lookup_failure_is_shown_and_keeps_fields", async () => {
  const a = api({ prInfo: vi.fn(async () => { throw new Error("Add GITHUB_TOKEN to ~/.kb/kb.env, or pick the branches yourself"); }) });
  renderApp(<TestPlans />, a);
  await userEvent.type(await screen.findByLabelText("sop-web base branch"), "master");
  await userEvent.type(screen.getByLabelText("sop-web PR link"), "https://github.com/acme/sop-web/pull/1");
  await userEvent.tab();
  expect(await screen.findByText(/Add GITHUB_TOKEN/)).toBeInTheDocument();
  expect(screen.getByLabelText("sop-web base branch")).toHaveValue("master");
});

test("pr_link_lookup_runs_only_when_the_link_changes", async () => {
  const a = api({ prInfo: vi.fn(async () => ({ title: "T", base_ref: "develop", head_ref: "fix" })) });
  renderApp(<TestPlans />, a);
  const link = await screen.findByLabelText("sop-backend PR link");
  await userEvent.type(link, "https://github.com/acme/sop-backend/pull/1");
  await userEvent.tab();
  await waitFor(() => expect(screen.getByLabelText("sop-backend base branch")).toHaveValue("develop"));
  const base = screen.getByLabelText("sop-backend base branch");
  await userEvent.clear(base);
  await userEvent.type(base, "release");
  await userEvent.click(link);
  await userEvent.tab();
  expect(a.prInfo).toHaveBeenCalledTimes(1);
  expect(base).toHaveValue("release");
});
