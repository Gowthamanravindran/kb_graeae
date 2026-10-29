import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { AskPayload, Job } from "../api/types";
import { useApp } from "../state";
import { fakeApi, renderApp } from "../test-utils";
import { Ask } from "./Ask";

const render_ = vi.fn();
vi.mock("mermaid", () => ({ default: { initialize: vi.fn(), render: (...a: unknown[]) => render_(...a) } }));
beforeEach(() => render_.mockReset().mockResolvedValue({ svg: "<svg data-diagram='1'></svg>" }));

const cite = (file: string, line: number) => ({ repo: "sop-web", file, line_start: line, line_end: line + 4, content_hash: "h" });
const PAYLOAD: AskPayload = {
  answer: "Scheduling stores a cron rule and a worker creates tasks.", reused: false, reused_flow_id: null, saved_qa_id: "qa1",
  suggestions: ["How are recurring tasks cancelled?"], warnings: [], mermaid: "flowchart LR\n A-->B", not_found: false,
  steps: [
    { side: "ui", text: "User opens the schedule dialog", verified: true, citation: cite("src/tasks/Schedule.tsx", 12) },
    { side: "ui", text: "Form submits the mutation", verified: true, citation: cite("src/tasks/api.ts", 40) },
    { side: "backend", text: "Resolver saves the rule", verified: true, citation: { ...cite("src/schedule.resolver.ts", 8), repo: "sop-backend" } },
    { side: "backend", text: "Worker sends reminders", verified: false, citation: null },
  ],
  how: { modules: ["Tasks", "Background jobs"], shortlist_size: 18, claude_calls: 2, files_read: 9, tool_steps: 14, seconds: 41.2, trace_id: "t1" },
};
const job = (id: string, state: Job["state"], result: AskPayload | null = null, step = "Reading the code"): Job<AskPayload> =>
  ({ id, kind: "ask", key: null, state, step, progress: 0.5, result, error: null, created_at: "" });

function Switch() {
  const { view, go } = useApp();
  return view === "ask" ? <><Ask /><button onClick={() => go("library")}>Leave</button></> : <button onClick={() => go("ask")}>Back</button>;
}

test("ask_renders_payload", async () => {
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", PAYLOAD)) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "How does scheduling work?", jobId: "j1" } });
  expect(await screen.findByText("3 of 4 steps verified")).toBeInTheDocument();
  expect(screen.getByText("How does scheduling work?")).toBeInTheDocument();
  expect(screen.getByText(/Scheduling stores a cron rule/)).toBeInTheDocument();
  const ui = screen.getByRole("region", { name: "UI flow" });
  expect(within(ui).getByText("User opens the schedule dialog")).toBeInTheDocument();
  const be = screen.getByRole("region", { name: "Backend flow" });
  expect(within(be).getByText("Unverified · no matching code found")).toBeInTheDocument();
  await waitFor(() => expect(document.querySelector("svg[data-diagram='1']")).not.toBeNull());
  const how = screen.getByRole("complementary", { name: "How this answer was made" });
  expect(within(how).getByText("Tasks, Background jobs")).toBeInTheDocument();
  expect(within(how).getByText("41.2 s")).toBeInTheDocument();
  expect(screen.getByText("Saved privately to your memory")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "How are recurring tasks cancelled?" })).toBeInTheDocument();
});

test("citation opens the file in the editor", async () => {
  const openCitation = vi.fn(async () => ({ ok: true }));
  window.kb = { openCitation } as unknown as Window["kb"];
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", PAYLOAD)) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  await userEvent.click(await screen.findByRole("button", { name: "sop-web:src/tasks/Schedule.tsx:12" }));
  expect(openCitation).toHaveBeenCalledWith({ project: "sop", branch: "main", repo: "sop-web", file: "src/tasks/Schedule.tsx", line: 12 });
  delete window.kb;
});

test("citation refused shows the reason", async () => {
  window.kb = { openCitation: vi.fn(async () => ({ ok: false, error: "file not found in your checkout of sop-web" })) } as unknown as Window["kb"];
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", PAYLOAD)) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  await userEvent.click(await screen.findByRole("button", { name: "sop-web:src/tasks/Schedule.tsx:12" }));
  expect(await screen.findByText("file not found in your checkout of sop-web")).toBeInTheDocument();
  delete window.kb;
});

test("ask_resumes_running_job", async () => {
  let done = false;
  const api = fakeApi({
    ask: vi.fn(async () => ({ job_id: "j2" })),
    job: vi.fn(async (id: string) => (done ? job(id, "done", PAYLOAD) : job(id, "running", null, "Tracing the backend"))),
  });
  renderApp(<Switch />, api, { project: "sop", branch: "main", view: "ask", askSeed: null });
  await userEvent.type(await screen.findByRole("textbox", { name: "Question" }), "How does scheduling work?");
  await userEvent.click(screen.getByRole("button", { name: "Ask" }));
  expect(await screen.findByText("Tracing the backend")).toBeInTheDocument();
  expect(api.ask).toHaveBeenCalledWith("sop", "main", { question: "How does scheduling work?", fromQa: null });
  await userEvent.click(screen.getByRole("button", { name: "Leave" }));
  done = true;
  await userEvent.click(screen.getByRole("button", { name: "Back" }));
  expect(await screen.findByText("3 of 4 steps verified")).toBeInTheDocument();
  expect(api.job).toHaveBeenLastCalledWith("j2");
});

test("ask_shows_claude_banner", async () => {
  const api = fakeApi({ status: vi.fn(async () => ({ version: "0.1.0", claude: { available: false, reason: "claude not found on PATH" }, langfuse: { configured: false } })) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: null });
  expect(await screen.findByText(/Claude Code isn't available on this computer \(claude not found on PATH\)/)).toBeInTheDocument();
  expect(screen.getByRole("textbox", { name: "Question" })).toBeDisabled();
});

test("reused answer says no Claude call", async () => {
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", { ...PAYLOAD, reused: true, reused_flow_id: "f1" })) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  expect(await screen.findByText("Reused a saved answer — no Claude call")).toBeInTheDocument();
});

test("share publishes the saved answer and flips the state", async () => {
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", PAYLOAD)), publish: vi.fn(async () => ({ published: 1 })) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  await userEvent.click(await screen.findByRole("button", { name: "Share to sop@main" }));
  expect(api.publish).toHaveBeenCalledWith("sop", "main", ["qa1"], false);
  expect(await screen.findByText("Shared")).toBeInTheDocument();
});

test("follow-up sends fromQa and a suggestion asks it", async () => {
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", PAYLOAD)), ask: vi.fn(async () => ({ job_id: "j9" })) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  await userEvent.type(await screen.findByRole("textbox", { name: "Ask a follow-up about this answer…" }), "And deletion?{Enter}");
  expect(api.ask).toHaveBeenCalledWith("sop", "main", { question: "And deletion?", fromQa: "qa1" });
  await userEvent.click(screen.getAllByRole("button", { name: "How are recurring tasks cancelled?" })[0]);
  expect(api.ask).toHaveBeenLastCalledWith("sop", "main", { question: "How are recurring tasks cancelled?", fromQa: null });
});

test("opening a saved answer shows it without asking", async () => {
  const api = fakeApi({
    qa: vi.fn(async () => ({ id: "qa1", question: "How does scheduling work?", answer: "Saved text.", visibility: "published", stale: false, author: "Priya", created_at: "", flow: { steps: PAYLOAD.steps, mermaid: "", module_ids: [] } })),
  });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { qaId: "qa1" } });
  expect(await screen.findByText("Saved text.")).toBeInTheDocument();
  expect(screen.getByText("Shared")).toBeInTheDocument();
  expect(api.ask).not.toHaveBeenCalled();
});

test("queued question shows its place in line", async () => {
  const api = fakeApi({ job: vi.fn(async (id: string) => ({ ...job(id, "queued", null, ""), queue_position: 2 })) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  expect(await screen.findByText("Queued behind other questions (position 2)")).toBeInTheDocument();
});

test("ask_share_signed_out_opens_login", async () => {
  const { ApiError } = await import("../api/client");
  const api = fakeApi({ job: vi.fn(async (id: string) => job(id, "done", PAYLOAD)), publish: vi.fn(async () => { throw new ApiError(401, "not signed in"); }) });
  renderApp(<Ask />, api, { project: "sop", branch: "main", view: "ask", askSeed: { question: "q", jobId: "j1" } });
  await userEvent.click(await screen.findByRole("button", { name: "Share to sop@main" }));
  expect(await screen.findByText("Sign in to the registry")).toBeInTheDocument();
});
