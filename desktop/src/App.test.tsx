import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Main } from "./App";
import { fakeApi, renderApp } from "./test-utils";

test("starting_an_analysis_shows_progress_at_once_in_the_real_shell", async () => {
  let answer: (v: { job_id: string }) => void = () => {};
  const a = fakeApi({
    branches: vi.fn(async () => ({ rows: [], registry_error: null })), // the server hasn't registered the job yet
    analyze: vi.fn(() => new Promise<{ job_id: string }>((r) => { answer = r; })), // and is slow to answer
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: "analyze:sop@main", state: "running", step: "Checking out code", progress: 0.02, result: null, error: null, created_at: "" })),
  });
  renderApp(<Main />, a, { project: "sop", branch: null, view: "library" });
  await userEvent.click(await screen.findByRole("button", { name: "Analyse a branch" }));
  await userEvent.type(screen.getByLabelText("Branch"), "main");
  await userEvent.click(screen.getByRole("button", { name: "Start analysis" }));
  const card = await screen.findByRole("region", { name: "Analysing main" }); // before the engine answers
  expect(within(card).getByText(/Starting/)).toBeInTheDocument();
  answer({ job_id: "j1" });
  await waitFor(() => expect(within(screen.getByRole("region", { name: "Analysing main" })).getByText("Checking out code")).toBeInTheDocument());
});

test("returning_to_the_library_shows_a_running_analysis_from_the_jobs_list", async () => {
  const a = fakeApi({
    branches: vi.fn(() => new Promise(() => {})), // the full branch list is slow on big projects
    jobs: vi.fn(async () => [{ id: "j7", kind: "analyze", key: "analyze:sop@production", state: "running", step: "Naming modules (3/40)", progress: 0.4, result: null, error: null, created_at: "" }]),
    job: vi.fn(async (id: string) => ({ id, kind: "analyze", key: "analyze:sop@production", state: "running", step: "Naming modules (3/40)", progress: 0.4, result: null, error: null, created_at: "" })),
  });
  renderApp(<Main />, a, { project: "sop", branch: "production", view: "library" });
  const card = await screen.findByRole("region", { name: "Analysing production" });
  expect(await within(card).findByText("Naming modules (3/40)")).toBeInTheDocument();
});
