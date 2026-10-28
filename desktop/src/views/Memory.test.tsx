import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { MemoryItem, MemoryView } from "../api/types";
import { useApp } from "../state";
import { fakeApi, renderApp } from "../test-utils";
import { Memory } from "./Memory";

const item = (o: Partial<MemoryItem>): MemoryItem => ({
  qa_id: "q", question: "Q", answer_preview: "", state: "private", stale: false, author: "me@corp",
  created_at: new Date().toISOString(), modules: ["Tasks"], citations: 4, has_flow: true, ...o,
});
const VIEW: MemoryView = {
  items: [
    item({ qa_id: "q1", question: "How does scheduling work?" }),
    item({ qa_id: "q2", question: "How are reminders sent?", has_flow: false }),
    item({ qa_id: "q3", question: "How does login work?", state: "published", author: "priya@corp" }),
    item({ qa_id: "q4", question: "How is audit stored?", state: "published", stale: true }),
    item({ qa_id: "q5", question: "How are comments edited?", state: "queued" }),
  ],
  suggestions: [
    { id: "s1", question: "How are recurring tasks cancelled?", source_qa_id: "q1" },
    { id: "s2", question: "What happens when a reminder fails?", source_qa_id: "q2" },
  ],
  counts: { all: 5, private: 3, published: 2, stale: 1, suggested: 2 },
};

function Probe() {
  const { view, askSeed } = useApp();
  return <output data-testid="probe">{view}|{askSeed?.jobId ?? ""}|{askSeed?.qaId ?? ""}</output>;
}
const row = (q: string) => screen.getByText(q).closest("li")!;
const start = (over = {}) => {
  const api = fakeApi({ memory: vi.fn(async () => VIEW), ...over });
  renderApp(<><Memory /><Probe /></>, api, { project: "sop", branch: "main", view: "memory" });
  return api;
};

test("tab counts and filtering", async () => {
  start();
  expect(await screen.findByRole("heading", { name: "Memory · sop@main" })).toBeInTheDocument();
  for (const t of ["All · 5", "Mine, private · 3", "Published · 2", "Stale · 1", "Suggested · 2"]) expect(screen.getByRole("tab", { name: t })).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Published · 2" }));
  expect(screen.getByText("How does login work?")).toBeInTheDocument();
  expect(screen.queryByText("How does scheduling work?")).not.toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Stale · 1" }));
  expect(screen.getByText("How is audit stored?")).toBeInTheDocument();
  expect(screen.queryByText("How does login work?")).not.toBeInTheDocument();
});

test("rows show badge and detail line", async () => {
  start();
  await screen.findByText("How does scheduling work?");
  expect(within(row("How does scheduling work?")).getByText("Private")).toBeInTheDocument();
  expect(within(row("How does scheduling work?")).getByText(/Tasks · flow with diagram · 4 citations · me@corp/)).toBeInTheDocument();
  expect(within(row("How are reminders sent?")).getByText(/Tasks · answer · 4 citations/)).toBeInTheDocument();
  expect(within(row("How does login work?")).getByText("Published")).toBeInTheDocument();
  expect(within(row("How are comments edited?")).getByText("Queued")).toBeInTheDocument();
  expect(within(row("How is audit stored?")).getByText("Stale")).toBeInTheDocument();
  expect(within(row("How does login work?")).queryByRole("checkbox")).not.toBeInTheDocument();
});

test("selecting two private items shares both", async () => {
  const api = start({ publish: vi.fn(async () => ({})) });
  await screen.findByText("How does scheduling work?");
  expect(screen.getByRole("button", { name: "Share 0 selected" })).toBeDisabled();
  await userEvent.click(within(row("How does scheduling work?")).getByRole("checkbox"));
  await userEvent.click(within(row("How are reminders sent?")).getByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: "Share 2 selected" }));
  expect(api.publish).toHaveBeenCalledWith("sop", "main", ["q1", "q2"], false);
  await waitFor(() => expect(api.memory).toHaveBeenCalledTimes(2));
});

test("stale row re-traces into Ask", async () => {
  const api = start({ retrace: vi.fn(async () => ({ job_id: "jr" })) });
  await screen.findByText("How is audit stored?");
  await userEvent.click(within(row("How is audit stored?")).getByRole("button", { name: "Re-trace" }));
  expect(api.retrace).toHaveBeenCalledWith("sop", "main", "q4");
  expect(screen.getByTestId("probe")).toHaveTextContent("ask|jr|");
});

test("Ask this opens the saved answer", async () => {
  start();
  await userEvent.click(within(await screen.findByText("How does login work?").then((e) => e.closest("li")!)).getByRole("button", { name: "Ask this" }));
  expect(screen.getByTestId("probe")).toHaveTextContent("ask||q3");
});

test("running a suggestion starts a job and shows it in Ask", async () => {
  const api = start({ runSuggestion: vi.fn(async () => ({ job_id: "js" })) });
  const panel = await screen.findByRole("complementary", { name: "Suggested next" });
  expect(within(panel).getByText(/Nothing runs until you choose/)).toBeInTheDocument();
  expect(within(panel).getByRole("button", { name: "Run all 2" })).toBeInTheDocument();
  await userEvent.click(within(within(panel).getByText("How are recurring tasks cancelled?").closest("li")!).getByRole("button", { name: "Run" }));
  expect(api.runSuggestion).toHaveBeenCalledWith("sop", "main", "s1");
  expect(screen.getByTestId("probe")).toHaveTextContent("ask|js|");
});

test("queued items offer Sync now", async () => {
  const api = start({ sync: vi.fn(async () => ({ sent: 1, left: 0, warnings: [] })) });
  await userEvent.click(within(await screen.findByText("How are comments edited?").then((e) => e.closest("li")!)).getByRole("button", { name: "Sync now" }));
  expect(api.sync).toHaveBeenCalledWith("sop", "main");
  expect(await screen.findByText("Sent 1 item to the registry.")).toBeInTheDocument();
});
