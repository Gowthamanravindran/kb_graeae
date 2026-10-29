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
