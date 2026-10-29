import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { SettingsView } from "../api/types";
import { fakeApi, renderApp } from "../test-utils";
import { Settings } from "./Settings";

const empty = { set: false, last4: null, source: null };
const VIEW: SettingsView = {
  env_path: "/Users/me/.kb/kb.env",
  values: {
    LANGFUSE_PUBLIC_KEY: { set: true, last4: "9999", source: "file" },
    LANGFUSE_SECRET_KEY: { set: true, last4: "1234", source: "file" },
    LANGFUSE_HOST: { set: true, last4: "3000", source: "file", value: "http://localhost:3000" },
    GITHUB_TOKEN: empty,
  },
};

afterEach(() => { delete window.kb; });

test("shows_masked_and_saves", async () => {
  const api = fakeApi({ settings: vi.fn(async () => VIEW), saveSettings: vi.fn(async () => VIEW) });
  renderApp(<Settings />, api, { project: "sop", branch: "main", view: "settings" });
  const secret = await screen.findByLabelText("Secret key");
  expect(secret).toHaveAttribute("placeholder", "••••1234");
  expect(screen.getByLabelText("Host")).toHaveValue("http://localhost:3000");
  await userEvent.type(screen.getByLabelText("Public key"), "pk-lf-new");
  await userEvent.click(screen.getByRole("button", { name: "Save" }));
  expect(api.saveSettings).toHaveBeenCalledWith({ LANGFUSE_PUBLIC_KEY: "pk-lf-new" });
  expect(await screen.findByText("Saved. New questions use these settings.")).toBeInTheDocument();
});

test("environment_value_is_read_only", async () => {
  const v = { ...VIEW, values: { ...VIEW.values, GITHUB_TOKEN: { set: true, last4: "abcd", source: "environment" as const } } };
  renderApp(<Settings />, fakeApi({ settings: vi.fn(async () => v) }), { project: "sop", branch: "main", view: "settings" });
  expect(await screen.findByLabelText("GitHub token")).toBeDisabled();
  expect(screen.getByText("Set in your environment; that value wins over this file.")).toBeInTheDocument();
});

test("registry_section_lists_projects", async () => {
  const revealConfig = vi.fn(async () => ({ ok: true }));
  window.kb = { revealConfig } as unknown as Window["kb"];
  const api = fakeApi({
    settings: vi.fn(async () => VIEW),
    logout: vi.fn(async () => ({ forgotten: true })),
  });
  renderApp(<Settings />, api, { project: "sop", branch: "main", view: "settings" });
  const reg = await screen.findByRole("region", { name: "Registries" });
  expect(await within(reg).findByText("http://reg")).toBeInTheDocument();
  expect(await within(reg).findByText("Signed in as me@corp")).toBeInTheDocument();
  await userEvent.click(within(reg).getByRole("button", { name: "Sign out" }));
  expect(api.logout).toHaveBeenCalledWith("http://reg");
  await userEvent.click(within(reg).getByRole("button", { name: "Show kb.yaml" }));
  expect(revealConfig).toHaveBeenCalledWith("sop");
});

test("test_connection_rechecks_status", async () => {
  const api = fakeApi({ settings: vi.fn(async () => VIEW) });
  renderApp(<Settings />, api, { project: "sop", branch: "main", view: "settings" });
  await userEvent.click(await screen.findByRole("button", { name: "Test connection" }));
  await waitFor(() => expect(api.status).toHaveBeenCalledWith(true));
});
