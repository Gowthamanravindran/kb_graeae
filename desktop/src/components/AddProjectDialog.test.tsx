import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api/client";
import { fakeApi, renderApp } from "../test-utils";
import { AddProjectDialog } from "./AddProjectDialog";

const INSPECTED = {
  repos: [
    { source: "https://github.com/acme/sop-fe-master", kind: "url", name: "sop-web", role: "frontend", is_git: true, default_branch: "master",
      branches: ["master", "release"], path: "packages/sop-web", path_options: [{ path: "packages/sop-web", name: "sop-web", role: "frontend" }, { path: "packages/ui-kit", name: "ui-kit", role: "frontend" }], context_docs: [] },
    { source: "/Users/me/sop-backend-main", kind: "local", name: "sop-backend", role: "backend", is_git: true, default_branch: "develop",
      branches: ["develop", "main"], path: null, path_options: [], context_docs: ["CONTEXT.md"] },
  ],
  linkers: [
    { type: "graphql", from: "sop-web", to: "sop-backend", options: { backend_style: "graphql-compose" } },
    { type: "react-router", in: "sop-web" },
    { type: "bull-queue", in: "sop-backend" },
  ],
};

function api(over = {}) {
  return fakeApi({
    inspectProject: vi.fn(async () => ({ job_id: "ji" })),
    job: vi.fn(async (id: string) => ({ id, kind: "inspect", key: null, state: "done", step: "", progress: 1, result: INSPECTED, error: null, created_at: "" })),
    createProject: vi.fn(async () => ({ name: "sop", config_path: "/h/projects/sop/kb.yaml", registry: null, repos: [] })),
    ...over,
  });
}

async function readRepos(a: ReturnType<typeof api>) {
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, a);
  await userEvent.type(screen.getByLabelText("Project name"), "sop");
  await userEvent.type(screen.getByLabelText("Repo 1"), "https://github.com/acme/sop-fe-master");
  await userEvent.click(screen.getByRole("button", { name: "Add another repo" }));
  await userEvent.type(screen.getByLabelText("Repo 2"), "/Users/me/sop-backend-main");
  await userEvent.click(screen.getByRole("button", { name: "Read repos" }));
  expect(a.inspectProject).toHaveBeenCalledWith(["https://github.com/acme/sop-fe-master", "/Users/me/sop-backend-main"]);
  await screen.findByRole("button", { name: "Create project" });
}

test("asks_for_repo_links_or_folders_not_a_yaml", async () => {
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, api());
  expect(screen.getByLabelText("Repo 1")).toHaveAttribute("placeholder", expect.stringMatching(/GitHub link or folder/));
  expect(screen.getByRole("button", { name: "Read repos" })).toBeDisabled();
  expect(screen.getByRole("button", { name: /existing kb.yaml/ })).toBeInTheDocument();
});

test("folder_picker_fills_the_row", async () => {
  (window as any).kb = { pickFolder: vi.fn(async () => "/Users/me/sop-backend-main") };
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, api());
  await userEvent.click(screen.getByRole("button", { name: "Choose folder for repo 1" }));
  await waitFor(() => expect(screen.getByLabelText("Repo 1")).toHaveValue("/Users/me/sop-backend-main"));
  delete (window as any).kb;
});

test("review_shows_detected_setup_and_creates", async () => {
  const a = api();
  await readRepos(a);
  expect(screen.getByLabelText("Name of repo 1")).toHaveValue("sop-web");
  expect(screen.getByLabelText("Package in repo 1")).toHaveValue("packages/sop-web");
  expect(screen.getByLabelText("Default branch of repo 2")).toHaveValue("develop");
  const links = screen.getByRole("group", { name: "How the repos connect" });
  expect(within(links).getByRole("checkbox", { name: /GraphQL: sop-web calls sop-backend/ })).toBeChecked();
  // rename a repo and untick Bull: linkers follow the new name
  await userEvent.clear(screen.getByLabelText("Name of repo 2"));
  await userEvent.type(screen.getByLabelText("Name of repo 2"), "api");
  await userEvent.click(within(links).getByRole("checkbox", { name: /Bull queues in api/ }));
  await userEvent.selectOptions(screen.getByLabelText("Package in repo 1"), "");
  await userEvent.click(screen.getByRole("button", { name: "Create project" }));
  expect(a.createProject).toHaveBeenCalledWith({
    project: "sop",
    repos: [
      { name: "sop-web", role: "frontend", source: "https://github.com/acme/sop-fe-master", path: null, default_branch: "master" },
      { name: "api", role: "backend", source: "/Users/me/sop-backend-main", path: null, default_branch: "develop" },
    ],
    linkers: [
      { type: "graphql", from: "sop-web", to: "api", options: { backend_style: "graphql-compose" } },
      { type: "react-router", in: "sop-web" },
    ],
    context_docs: ["api:CONTEXT.md"],
  });
});

test("create_error_is_shown", async () => {
  const a = api({ createProject: vi.fn(async () => { throw new ApiError(400, "a project named sop already exists"); }) });
  await readRepos(a);
  await userEvent.click(screen.getByRole("button", { name: "Create project" }));
  expect(await screen.findByText("a project named sop already exists")).toBeInTheDocument();
});

test("inspect_failure_is_shown_on_the_first_step", async () => {
  const a = api({ job: vi.fn(async (id: string) => ({ id, kind: "inspect", key: null, state: "failed", step: "", progress: 0, result: null, error: "could not read https://github.com/acme/x: Repository not found", created_at: "" })) });
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, a);
  await userEvent.type(screen.getByLabelText("Project name"), "sop");
  await userEvent.type(screen.getByLabelText("Repo 1"), "https://github.com/acme/x");
  await userEvent.click(screen.getByRole("button", { name: "Read repos" }));
  expect(await screen.findByText(/Repository not found/)).toBeInTheDocument();
  expect(screen.getByLabelText("Repo 1")).toBeInTheDocument();
});

test("plain_folder_warns_about_pr_plans", async () => {
  const plain = { ...INSPECTED, repos: [{ ...INSPECTED.repos[1], is_git: false, default_branch: null, branches: [] }] };
  const a = api({ job: vi.fn(async (id: string) => ({ id, kind: "inspect", key: null, state: "done", step: "", progress: 1, result: plain, error: null, created_at: "" })) });
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, a);
  await userEvent.type(screen.getByLabelText("Project name"), "sop");
  await userEvent.type(screen.getByLabelText("Repo 1"), "/x");
  await userEvent.click(screen.getByRole("button", { name: "Read repos" }));
  expect(await screen.findByText(/not a git clone/)).toBeInTheDocument();
});

test("existing_yaml_still_works", async () => {
  (window as any).kb = { pickFile: vi.fn(async () => "/x/bad.yaml") };
  const a = api({ addProject: vi.fn(async () => { throw new ApiError(400, "cannot read /x/bad.yaml: bad yaml"); }) });
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, a);
  await userEvent.click(screen.getByRole("button", { name: /existing kb.yaml/ }));
  expect(await screen.findByText("cannot read /x/bad.yaml: bad yaml")).toBeInTheDocument();
  delete (window as any).kb;
});

test("monorepo_apps_each_get_a_card_and_can_be_removed", async () => {
  const fe = "/Users/me/sop-fe";
  const two = {
    repos: [
      { ...INSPECTED.repos[0], source: fe, kind: "local", name: "sop-web", path: "packages/sop-web" },
      { ...INSPECTED.repos[0], source: fe, kind: "local", name: "sop-mobile", path: "packages/sop-mobile" },
      INSPECTED.repos[1],
    ],
    linkers: [
      { type: "graphql", from: ["sop-web", "sop-mobile"], to: "sop-backend", options: { backend_style: "graphql-compose" } },
      { type: "react-router", in: "sop-web" },
    ],
  };
  const a = api({ job: vi.fn(async (id: string) => ({ id, kind: "inspect", key: null, state: "done", step: "", progress: 1, result: two, error: null, created_at: "" })) });
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, a);
  await userEvent.type(screen.getByLabelText("Project name"), "sop");
  await userEvent.type(screen.getByLabelText("Repo 1"), fe);
  await userEvent.click(screen.getByRole("button", { name: "Read repos" }));
  const links = await screen.findByRole("group", { name: "How the repos connect" });
  expect(within(links).getByRole("checkbox", { name: "GraphQL: sop-web, sop-mobile call sop-backend" })).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Remove sop-mobile" }));
  expect(within(links).getByRole("checkbox", { name: "GraphQL: sop-web calls sop-backend" })).toBeChecked();
  await userEvent.click(screen.getByRole("button", { name: "Create project" }));
  const body = vi.mocked(a.createProject).mock.calls[0][0];
  expect(body.repos.map((r) => [r.name, r.path])).toEqual([["sop-web", "packages/sop-web"], ["sop-backend", null]]);
  expect(body.linkers[0]).toEqual({ type: "graphql", from: "sop-web", to: "sop-backend", options: { backend_style: "graphql-compose" } });
});

test("describes_rest_and_broker_links", async () => {
  const result = { ...INSPECTED, linkers: [{ type: "rest", from: ["sop-web", "sop-backend"], to: "sop-backend" },
    { type: "kafka", in: ["sop-web", "sop-backend"] }, { type: "sqs", in: "sop-backend" }, { type: "rabbitmq", in: "sop-backend" }] };
  await readRepos(api({ job: vi.fn(async (id: string) => ({ id, kind: "inspect", key: null, state: "done", step: "", progress: 1, result, error: null, created_at: "" })) }));
  for (const name of ["REST: sop-web, sop-backend call sop-backend", "Kafka topics across sop-web, sop-backend", "SQS queues in sop-backend", "RabbitMQ queues in sop-backend"])
    expect(screen.getByRole("checkbox", { name })).toBeInTheDocument();
});
