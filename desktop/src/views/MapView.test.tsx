import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MapView as MapPayload } from "../api/types";
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
