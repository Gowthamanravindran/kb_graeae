import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { Connections, MapView as MapPayload } from "../api/types";
import { Mermaid } from "../components/Mermaid";
import { useApp } from "../state";
import { fakeApi, renderApp } from "../test-utils";
import { MapView } from "./MapView";

const render_ = vi.fn();
let mermaidFails = false; // a plain flag: a vi.fn returning a rejected promise trips vitest's unhandled-rejection check
vi.mock("mermaid", () => ({
  default: {
    initialize: vi.fn(),
    render: (...a: unknown[]) => (mermaidFails ? Promise.reject(new Error("Parse error")) : render_(...a)),
  },
}));

const MAP: MapPayload = {
  apps: [{ repo: "sop-web", role: "frontend", label: "sop-web" }],
  modules: [
    { id: "m-tasks", name: "Tasks", summary: "Create, assign and schedule tasks.", layer: "product", size: 40, top_paths: ["sop-web:src/tasks"], entry_points: [{ id: "e1", name: "TaskScheduler", kind: "function", file: "src/tasks/TaskScheduler.tsx", line: 12 }], answered: [{ qa_id: "q1", question: "How does scheduling work?", author: "Priya", visibility: "published" }] },
    { id: "m-jobs", name: "Background jobs", summary: "Queues and workers.", layer: "behind", size: 20, top_paths: ["sop-backend:src/queues"], entry_points: [], answered: [] },
  ],
  edges: [{ source: "m-tasks", target: "m-jobs", count: 7 }],
  system_md: "SOP is a task app.\n\n```mermaid\nflowchart LR\n  A-->B\n```\n",
};

function Probe() {
  const { view, askSeed } = useApp();
  return <output data-testid="probe">{view}|{askSeed?.question ?? ""}|{askSeed?.qaId ?? ""}</output>;
}

beforeEach(() => {
  mermaidFails = false;
  render_.mockReset().mockResolvedValue({ svg: "<svg data-ok='1'></svg>" });
});

test("title, subtitle and code-names toggle", async () => {
  renderApp(<MapView />, fakeApi({ map: vi.fn(async () => MAP) }), { project: "sop", branch: "main", view: "map" });
  expect(await screen.findByRole("heading", { name: "How sop is put together" })).toBeInTheDocument();
  expect(screen.getByText("main · 2 modules")).toBeInTheDocument();
  expect(screen.getByText("Where people use it")).toBeInTheDocument();
  expect(screen.getByText("Behind the scenes")).toBeInTheDocument();
  expect(screen.getAllByText("Tasks").length).toBeGreaterThan(0);
  await userEvent.click(screen.getByRole("radio", { name: "Code names" }));
  expect(await screen.findByText("m-tasks")).toBeInTheDocument();
  expect(screen.getAllByText("sop-web:src/tasks").length).toBeGreaterThan(0);
});

test("selecting a module fills the panel and Ask about routes to Ask", async () => {
  renderApp(<><MapView /><Probe /></>, fakeApi({ map: vi.fn(async () => MAP) }), { project: "sop", branch: "main", view: "map" });
  await userEvent.click(await screen.findByRole("button", { name: /^Tasks/ }));
  const panel = screen.getByRole("complementary", { name: "Module details" });
  expect(within(panel).getByText("Create, assign and schedule tasks.")).toBeInTheDocument();
  expect(within(panel).getByText("src/tasks/TaskScheduler.tsx:12")).toBeInTheDocument();
  await userEvent.click(within(panel).getByRole("button", { name: "Ask about Tasks" }));
  expect(screen.getByTestId("probe")).toHaveTextContent("ask|How does Tasks work?|");
});

test("already-explained question opens the saved answer", async () => {
  renderApp(<><MapView /><Probe /></>, fakeApi({ map: vi.fn(async () => MAP) }), { project: "sop", branch: "main", view: "map" });
  await userEvent.click(await screen.findByRole("button", { name: /^Tasks/ }));
  await userEvent.click(screen.getByRole("button", { name: "How does scheduling work?" }));
  expect(screen.getByTestId("probe")).toHaveTextContent("ask||q1");
});

test("system overview renders markdown and the diagram", async () => {
  renderApp(<MapView />, fakeApi({ map: vi.fn(async () => MAP) }), { project: "sop", branch: "main", view: "map" });
  await userEvent.click(await screen.findByRole("button", { name: /System overview/ }));
  expect(screen.getByText("SOP is a task app.")).toBeInTheDocument();
  await waitFor(() => expect(document.querySelector("svg[data-ok='1']")).not.toBeNull());
  expect(render_.mock.calls[0][1]).toContain("flowchart LR");
});

test("Mermaid falls back to the code on invalid input", async () => {
  mermaidFails = true;
  render(<Mermaid code={"not a diagram"} />);
  expect(await screen.findByText("not a diagram", { selector: "pre" })).toBeInTheDocument();
});

test("no branch selected asks to pick one", async () => {
  renderApp(<MapView />, fakeApi(), { project: "sop", branch: null, view: "map" });
  expect(await screen.findByText(/Open a branch from the Library/)).toBeInTheDocument();
});

const CONN: Connections = {
  lanes: [{ id: "sop-web", label: "sop-web", role: "frontend" }, { id: "sop-backend", label: "sop-backend", role: "backend" }, { id: "jobs", label: "Background jobs", role: "jobs" }],
  nodes: [
    { id: "sop-web::m-tasks", lane: "sop-web", kind: "module", module_id: "m-tasks", name: "Tasks", code_name: "m-tasks", links: 2,
      routes: [{ path: "/tasks/create-task", repo: "sop-web", file: "src/App.tsx", line: 8 }] },
    { id: "sop-backend::m-tasks", lane: "sop-backend", kind: "module", module_id: "m-tasks", name: "Tasks", code_name: "m-tasks", links: 2, routes: [] },
    { id: "jobs::sop-backend::m-jobs", lane: "jobs", kind: "jobs", module_id: null, name: "Background jobs jobs", code_name: "m-jobs (jobs)", links: 1, routes: [] },
  ],
  edges: [
    { id: "e1", source: "sop-web::m-tasks", target: "sop-backend::m-tasks", kind: "graphql", count: 2, items: [
      { label: "AddTask", from: { repo: "sop-web", file: "src/tasks/TaskCreation.tsx", line: 14 }, to: { repo: "sop-backend", file: "src/tasks/resolvers.ts", line: 6 } },
      { label: "UpdateTask", from: { repo: "sop-web", file: "src/tasks/TaskEdit.tsx", line: 9 }, to: { repo: "sop-backend", file: "src/tasks/resolvers.ts", line: 30 } }] },
    { id: "e2", source: "sop-backend::m-tasks", target: "jobs::sop-backend::m-jobs", kind: "queue", count: 1, items: [
      { label: "TASK_REPEAT", from: { repo: "sop-backend", file: "src/tasks/resolvers.ts", line: 20 }, to: { repo: "sop-backend", file: "src/queues/repeat.ts", line: 3 } }] },
  ],
  unresolved: { graphql: 3 },
};

test("graph_view_shows_how_repos_connect", async () => {
  const openCitation = vi.fn(async () => ({ ok: true }));
  (window as any).kb = { openCitation };
  const a = fakeApi({ map: vi.fn(async () => MAP), connections: vi.fn(async () => CONN) });
  renderApp(<MapView />, a, { project: "sop", branch: "main", view: "map" });
  await userEvent.click(await screen.findByRole("radio", { name: "Graph" }));
  expect(a.connections).toHaveBeenCalledWith("sop", "main");
  expect(await screen.findByText("Background jobs", { selector: "div" })).toBeInTheDocument();
  expect(screen.getByText(/3 GraphQL calls couldn't be matched/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: /^Tasks.*sop-web/ }));
  const panel = screen.getByRole("complementary", { name: "Connection details" });
  expect(within(panel).getByText("/tasks/create-task")).toBeInTheDocument();
  await userEvent.click(within(panel).getByRole("button", { name: /GraphQL.*sop-backend.*2/ }));
  expect(within(panel).getByText("AddTask")).toBeInTheDocument();
  await userEvent.click(within(panel).getByRole("button", { name: "src/tasks/TaskCreation.tsx:14" }));
  expect(openCitation).toHaveBeenCalledWith({ project: "sop", branch: "main", repo: "sop-web", file: "src/tasks/TaskCreation.tsx", line: 14 });
  delete (window as any).kb;
});

test("graph_view_with_no_links_explains_why", async () => {
  const a = fakeApi({ map: vi.fn(async () => MAP), connections: vi.fn(async () => ({ lanes: [], nodes: [], edges: [], unresolved: {} })) });
  renderApp(<MapView />, a, { project: "sop", branch: "main", view: "map" });
  await userEvent.click(await screen.findByRole("radio", { name: "Graph" }));
  expect(await screen.findByText(/No links between the repos were found/)).toBeInTheDocument();
});

test("graph_layout_keeps_each_repo_in_one_column", async () => {
  const { graphLayout } = await import("./ConnectionsGraph");
  const many = { ...CONN, nodes: Array.from({ length: 30 }, (_, i) => ({ ...CONN.nodes[1], id: `sop-backend::m${i}` })), edges: [] };
  const { nodes } = graphLayout(many, null, null, () => {});
  const xs = new Set(nodes.filter((n) => n.type === "box").map((n) => n.position.x));
  expect(xs.size).toBe(1); // a second column would put the first column's lines behind its boxes
});
