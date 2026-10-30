import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { BranchRow } from "../api/types";
import { fakeApi, renderApp } from "../test-utils";
import { Library } from "./Library";

const local = { version: 1, status: "complete", commits: { "sop-web": "a1b2c3d4e5f6", "sop-backend": "9f8e7d6c5b4a" }, author: "Priya", created_at: "2026-10-26T10:00:00Z", modules: 14, qa_total: 23, qa_published: 23, synced: true, stats: { links_resolved: 312, links_unresolved: 14 } };
const remote = { latest_version: 1, overlay_count: 23, created_by: "priya@corp", commits: local.commits };
const ROWS: BranchRow[] = [
  { branch: "main", local, remote, state: "synced", job_id: null },
  { branch: "release/2.4", local: null, remote: { ...remote, overlay_count: 8 }, state: "not-pulled", job_id: null },
  { branch: "feature/recurring-tasks", local: { ...local, synced: false, qa_total: 3, qa_published: 0 }, remote: null, state: "local-only", job_id: null },
  { branch: "dev", local, remote: { ...remote, latest_version: 2 }, state: "update-available", job_id: null },
  { branch: "feature/audit-v3", local: null, remote: null, state: "in-progress", job_id: "jA" },
];

function api(over = {}) {
  return fakeApi({
    branches: vi.fn(async () => ({ rows: ROWS, registry_error: null })),
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: null, state: "running", step: "Naming modules (9/14)", progress: 0.64, result: null, error: null, created_at: "" })),
    ...over,
  });
}

function row(name: string) {
  return screen.getByText(name, { selector: "div" }).closest("tr")!;
}

test("rows render the right badge and action for each state", async () => {
  renderApp(<Library />, api());
  await screen.findByText("main", { selector: "div" });
  expect(within(row("main")).getByText("Synced")).toBeInTheDocument();
  expect(within(row("main")).getByRole("button", { name: "Open" })).toBeInTheDocument();
  expect(within(row("release/2.4")).getByText("Not pulled")).toBeInTheDocument();
  expect(within(row("release/2.4")).getByRole("button", { name: "Pull" })).toBeInTheDocument();
  expect(within(row("feature/recurring-tasks")).getByText("Private · local only")).toBeInTheDocument();
  expect(within(row("feature/recurring-tasks")).getByRole("button", { name: "Push to registry" })).toBeInTheDocument();
  expect(within(row("dev")).getByText("Update available")).toBeInTheDocument();
  expect(within(row("dev")).getByRole("button", { name: "Pull" })).toBeInTheDocument();
  expect(within(row("feature/audit-v3")).getByText("In progress")).toBeInTheDocument();
  expect(await within(row("feature/audit-v3")).findByText("Naming modules (9/14)")).toBeInTheDocument();
  expect(within(row("main")).getByText("14 modules")).toBeInTheDocument();
  expect(within(row("main")).getByText("23 Q&As")).toBeInTheDocument();
});

test("pull starts a job and shows its progress", async () => {
  const a = api({ pull: vi.fn(async () => ({ job_id: "jP" })) });
  renderApp(<Library />, a);
  await screen.findByText("release/2.4", { selector: "div" });
  await userEvent.click(within(row("release/2.4")).getByRole("button", { name: "Pull" }));
  expect(a.pull).toHaveBeenCalledWith("sop", "release/2.4", false);
  expect(await within(row("release/2.4")).findByRole("progressbar")).toBeInTheDocument();
});

test("analyse dialog submits branch and resume", async () => {
  const a = api({ analyze: vi.fn(async () => ({ job_id: "jN" })) });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  await userEvent.type(screen.getByLabelText("Branch"), "feature/x");
  await userEvent.click(screen.getByLabelText("Resume where it stopped"));
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(a.analyze).toHaveBeenCalledWith("sop", "feature/x", true);
});

test("registry error strip appears", async () => {
  renderApp(<Library />, api({ branches: vi.fn(async () => ({ rows: ROWS.slice(0, 1), registry_error: "registry unreachable: http://reg" })) }));
  expect(await screen.findByRole("alert")).toHaveTextContent("registry unreachable");
});

test("search filters branches and Open goes to the map", async () => {
  renderApp(<Library />, api());
  await screen.findByText("main", { selector: "div" });
  await userEvent.type(screen.getByLabelText("Search branches"), "release");
  await waitFor(() => expect(screen.queryByText("main", { selector: "div" })).not.toBeInTheDocument());
});

test("analyse_dialog_per_repo_branches", async () => {
  const a = api({ analyze: vi.fn(async () => ({ job_id: "j9" })) });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  await userEvent.click(screen.getByLabelText("Different branch per repo"));
  await userEvent.type(screen.getByLabelText("sop-web branch"), "master");
  await userEvent.type(screen.getByLabelText("sop-backend branch"), "develop");
  await userEvent.type(screen.getByLabelText("Name for this analysis"), "production");
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(a.analyze).toHaveBeenCalledWith("sop", "production", false, { "sop-web": "master", "sop-backend": "develop" });
});

test("rows_show_per_repo_branches", async () => {
  const prod = { ...local, branches: { "sop-web": "master", "sop-backend": "develop" } };
  renderApp(<Library />, api({ branches: vi.fn(async () => ({ rows: [{ branch: "production", local: prod, remote: null, state: "local-only", job_id: null }], registry_error: null })) }));
  expect(await screen.findByText("sop-web master · sop-backend develop")).toBeInTheDocument();
});

test("library_shows_plan_count", async () => {
  const withPlans = { ...local, testplans: 2 };
  renderApp(<Library />, api({ branches: vi.fn(async () => ({ rows: [{ branch: "main", local: withPlans, remote: null, state: "local-only", job_id: null }], registry_error: null })) }));
  expect(await screen.findByText("2 test plans")).toBeInTheDocument();
});

test("first_analysis_shows_progress_with_a_spinner_before_any_row_exists", async () => {
  const a = api({
    branches: vi.fn(async () => ({ rows: [], registry_error: null })),
    analyze: vi.fn(async () => ({ job_id: "jN" })),
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: null, state: "running", step: "Checking out code", progress: 0.02, result: null, error: null, created_at: "" })),
  });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  await userEvent.type(screen.getByLabelText("Branch"), "main");
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  const r = await waitFor(() => row("main"));
  expect(within(r).getByText("Checking out code")).toBeInTheDocument();
  expect(within(r).getByRole("status", { name: "Working" })).toBeInTheDocument();
  expect(screen.queryByText(/No analysed branches yet/)).not.toBeInTheDocument();
});

test("first_analysis_failure_is_shown", async () => {
  const a = api({
    branches: vi.fn(async () => ({ rows: [], registry_error: null })),
    analyze: vi.fn(async () => ({ job_id: "jF" })),
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: null, state: "failed", step: "", progress: 0, result: null, error: "no branch or commit 'main' in sop-backend", created_at: "" })),
  });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  await userEvent.type(screen.getByLabelText("Branch"), "main");
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(await screen.findByText(/no branch or commit 'main' in sop-backend/)).toBeInTheDocument();
});

test("analyse_dialog_prefills_production_branches", async () => {
  const a = api({
    projects: vi.fn(async () => [{ name: "sop", config_path: "/x", registry: null, linkers: [],
      repos: [{ name: "sop-web", role: "frontend", default_branch: "master" }, { name: "sop-backend", role: "backend", default_branch: "develop" }] }]),
    branches: vi.fn(async () => ({ rows: [], registry_error: null })),
    analyze: vi.fn(async () => ({ job_id: "jP" })),
  });
  renderApp(<Library />, a);
  await screen.findByText("sop-web");
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  expect(screen.getByRole("checkbox", { name: "Different branch per repo" })).toBeChecked();
  expect(screen.getByLabelText("sop-backend branch")).toHaveValue("develop");
  expect(screen.getByLabelText("Name for this analysis")).toHaveValue("production");
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  expect(a.analyze).toHaveBeenCalledWith("sop", "production", false, { "sop-web": "master", "sop-backend": "develop" });
});

test("delete_branch_confirms_with_what_is_lost", async () => {
  const a = api({ deleteBranch: vi.fn(async () => ({ job_id: "jd" })),
    job: vi.fn(async (id: string) => ({ id, kind: "delete", key: null, state: "done", step: "", progress: 1, result: {}, error: null, created_at: "" })) });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "More actions for main" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Delete…" }));
  const d = screen.getByRole("dialog", { name: "Delete main?" });
  expect(within(d).getByText(/23 saved answers/)).toBeInTheDocument();
  expect(within(d).getByText(/stays in the registry/)).toBeInTheDocument();
  await userEvent.click(within(d).getByRole("button", { name: "Delete" }));
  expect(a.deleteBranch).toHaveBeenCalledWith("sop", "main");
  await waitFor(() => expect(vi.mocked(a.branches).mock.calls.length).toBeGreaterThanOrEqual(3)); // reloaded once the delete finished
});

test("reanalyse_keeps_answers_and_start_fresh_drops_them", async () => {
  const a = api({ analyze: vi.fn(async () => ({ job_id: "jr" })) });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "More actions for feature/recurring-tasks" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Re-analyse" }));
  let d = screen.getByRole("dialog", { name: "Re-analyse feature/recurring-tasks?" });
  expect(within(d).getByText(/keeps the 3 saved answers/)).toBeInTheDocument();
  await userEvent.click(within(d).getByRole("button", { name: "Re-analyse" }));
  expect(a.analyze).toHaveBeenLastCalledWith("sop", "feature/recurring-tasks", false, undefined, false);
  await userEvent.click(await screen.findByRole("button", { name: "More actions for main" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Start fresh…" }));
  d = screen.getByRole("dialog", { name: "Start main from scratch?" });
  await userEvent.click(within(d).getByRole("button", { name: "Delete and analyse again" }));
  expect(a.analyze).toHaveBeenLastCalledWith("sop", "main", false, undefined, true);
});

test("busy_branch_delete_error_is_shown", async () => {
  const { ApiError } = await import("../api/client");
  const a = api({ deleteBranch: vi.fn(async () => { throw new ApiError(409, "main has a question or upload still running; try again when it finishes"); }) });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "More actions for main" }));
  await userEvent.click(screen.getByRole("menuitem", { name: "Delete…" }));
  await userEvent.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));
  expect(await screen.findByText(/still running/)).toBeInTheDocument();
});

test("running_analysis_has_a_progress_card_at_the_top", async () => {
  const a = api({
    branches: vi.fn(async () => ({ rows: [], registry_error: null })),
    analyze: vi.fn(async () => ({ job_id: "jB" })),
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: null, state: "running", step: "Building the code graph", progress: 0.1, result: null, error: null, created_at: new Date(Date.now() - 125_000).toISOString() })),
  });
  renderApp(<Library />, a);
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  await userEvent.type(screen.getByLabelText("Branch"), "main");
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  const card = await screen.findByRole("region", { name: "Analysing main" });
  expect(within(card).getByRole("progressbar")).toHaveAttribute("aria-valuenow", "10");
  expect(within(card).getByText("Building the code graph")).toBeInTheDocument();
  expect(within(card).getByText(/10% · 2 min/)).toBeInTheDocument();
});

test("analysis_started_elsewhere_appears_without_reopening", async () => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  let calls = 0;
  const a = api({
    jobs: vi.fn(async () => (++calls > 1 ? [{ id: "jX", kind: "analyze", key: "analyze:sop@production", state: "running", step: "", progress: 0, result: null, error: null, created_at: "" }] : [])),
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: null, state: "running", step: "Checking out code", progress: 0.02, result: null, error: null, created_at: "" })),
  });
  renderApp(<Library />, a);
  await screen.findByText("main", { selector: "div" });
  expect(screen.queryByRole("region", { name: "Analysing production" })).not.toBeInTheDocument();
  await vi.advanceTimersByTimeAsync(3_500); // the Library keeps watching the (fast) jobs list
  expect(await screen.findByRole("region", { name: "Analysing production" })).toBeInTheDocument();
  vi.useRealTimers();
});
