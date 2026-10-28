import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ApiError } from "../api/client";
import { fakeApi, renderApp } from "../test-utils";
import { AddProjectDialog } from "./AddProjectDialog";
import { LoginDialog } from "./LoginDialog";
import { Sidebar } from "./Sidebar";

test("sidebar highlights the active view and switches on click", async () => {
  renderApp(<Sidebar />, fakeApi());
  expect(screen.getByRole("link", { name: "Library" })).toHaveAttribute("aria-current", "page");
  await userEvent.click(screen.getByRole("link", { name: "Memory" }));
  expect(screen.getByRole("link", { name: "Memory" })).toHaveAttribute("aria-current", "page");
});

test("status footer explains when Claude Code is unavailable", async () => {
  const api = fakeApi({ status: vi.fn(async () => ({ version: "0.1.0", claude: { available: false, reason: "not signed in" }, langfuse: { configured: false } })) });
  renderApp(<Sidebar />, api);
  const item = await screen.findByText("Claude Code · not available");
  expect(item.closest("[title]")).toHaveAttribute("title", "not signed in");
  expect(await screen.findByText("Registry · reg")).toBeInTheDocument();
  expect(screen.getByText("Langfuse · off")).toBeInTheDocument();
});

test("add project dialog shows the config error", async () => {
  (window as any).kb = { pickFile: vi.fn(async () => "/x/bad.yaml") };
  const api = fakeApi({ addProject: vi.fn(async () => { throw new ApiError(400, "cannot read /x/bad.yaml: bad yaml"); }) });
  renderApp(<AddProjectDialog open onOpenChange={() => {}} />, api);
  await userEvent.click(screen.getByRole("button", { name: "Choose kb.yaml…" }));
  expect(await screen.findByText("cannot read /x/bad.yaml: bad yaml")).toBeInTheDocument();
});

test("login dialog offers single sign-on and shows the code", async () => {
  (window as any).kb = { openExternal: vi.fn(async () => {}) };
  const api = fakeApi({
    registry: vi.fn(async () => ({ url: "http://reg", logged_in: false, email: null, reachable: true, sso: true })),
    loginSso: vi.fn(async () => ({ job_id: "j9" })),
    job: vi.fn(async () => ({ id: "j9", kind: "login", key: null, state: "running", step: "Open https://idp/activate?code=AB-CD and confirm code AB-CD", progress: 0, result: null, error: null, created_at: "" })),
  });
  renderApp(<LoginDialog open onOpenChange={() => {}} />, api);
  await userEvent.click(await screen.findByRole("button", { name: "Sign in with single sign-on" }));
  expect(await screen.findByText(/AB-CD/)).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Open sign-in page" }));
  await waitFor(() => expect((window as any).kb.openExternal).toHaveBeenCalledWith("https://idp/activate?code=AB-CD"));
  expect(screen.getByRole("button", { name: "Use an API token instead" })).toBeInTheDocument();
});
