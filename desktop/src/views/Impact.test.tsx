import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { estimateCalls } from "../testplan";
import { useApp } from "../state";
import { fakeApi, renderApp } from "../test-utils";
import { DETAIL } from "./fixtures.testplan";
import { Impact } from "./Impact";

function Probe() {
  const { planScreen, planJobId } = useApp();
  return <output data-testid="probe">{planScreen}|{planJobId ?? ""}</output>;
}
const start = (over = {}) => {
  const api = fakeApi({ testplan: vi.fn(async () => DETAIL), generate: vi.fn(async () => ({ job_id: "jg" })), ...over });
  renderApp(<><Impact /><Probe /></>, api, { project: "sop", branch: "main", view: "testplans", askSeed: null, planId: "cs1", planScreen: "impact" });
  return api;
};

test("summary_and_flows", async () => {
  start();
  expect(await screen.findByRole("heading", { name: "Tasks: weekly repeat with end date" })).toBeInTheDocument();
  expect(screen.getByText("functions changed")).toBeInTheDocument();
  expect(screen.getByText(/isn't part of any module/)).toBeInTheDocument();
  const row = screen.getByText("Admin sets a task to repeat weekly").closest("li")!;
  expect(within(row).getByText("removed behaviour")).toBeInTheDocument();
  expect(within(row).getByText("Needs master trace")).toBeInTheDocument();
  await userEvent.click(within(row).getByText("2 changed decision points"));
  expect(within(row).getByText("Permission check `isAdmin`")).toBeInTheDocument();
});

test("counts_update_live", async () => {
  start();
  expect(await screen.findByText("Will generate 6 of 10")).toBeInTheDocument(); // P0 defaults to all 6 planned P0
  const p1 = screen.getByLabelText("P1 cases to generate now");
  await userEvent.clear(p1);
  await userEvent.type(p1, "2");
  expect(screen.getByText("Will generate 8 of 10")).toBeInTheDocument();
  await userEvent.type(screen.getByLabelText("Max per flow"), "3");
  expect(screen.getByText("Will generate 5 of 10")).toBeInTheDocument();
});

test("unticking_flow_reduces_planned", async () => {
  start();
  await screen.findByText("Will generate 6 of 10");
  await userEvent.click(screen.getByLabelText("Include Worker queues the next repeat"));
  expect(screen.getByText("Will generate 4 of 7")).toBeInTheDocument();
});

test("generate_calls_api_with_counts", async () => {
  const api = start();
  const p0 = await screen.findByLabelText("P0 cases to generate now");
  await userEvent.clear(p0);
  await userEvent.type(p0, "3");
  await userEvent.click(screen.getByRole("button", { name: "Generate" }));
  expect(api.generate).toHaveBeenCalledWith("sop", "cs1", { P0: 3, P1: 0, P2: 0, P3: 0 }, null);
  await waitFor(() => expect(screen.getByTestId("probe")).toHaveTextContent("board|jg"));
});

test("generate_disabled_without_claude", async () => {
  start({ status: vi.fn(async () => ({ version: "0.1.0", claude: { available: false, reason: "claude not found" }, langfuse: { configured: false } })) });
  expect(await screen.findByText(/Claude Code isn't available on this computer/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Generate" })).toBeDisabled();
});

test("estimate_matches_python_fixture", () => {
  const f = { ...DETAIL.impact!.flows[0], id: "f1", master_flow_id: null };
  const chosen = Array.from({ length: 20 }, (_, i) => ({ ...DETAIL.slots[0], id: `s${i}`, flow_id: "f1" }));
  expect(estimateCalls(chosen, [f], new Set(), new Set(), "pr")).toBe(1 + 1 + 1 + 2);
  expect(estimateCalls(chosen, [f], new Set(["f1:base", "f1:head"]), new Set(["f1|branches_edges"]), "pr")).toBe(2);
});
