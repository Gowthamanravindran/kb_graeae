import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { launch, makeHome } from "./fixtures";

test("app_starts_and_shows_library", async () => {
  const { home } = makeHome();
  const { app, page } = await launch({ KB_HOME: home });
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("mini", { exact: true }).first()).toBeVisible();
  await app.close();
});

test("sidecar_failure_shows_error_screen", async () => {
  const { home } = makeHome();
  const { app, page } = await launch({ KB_HOME: home, KB_SERVE_CMD: "false" });
  await expect(page.getByRole("heading", { name: "KB engine didn't start" })).toBeVisible({ timeout: 30_000 });
  await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();
  await app.close();
});

test("quitting_kills_engine", async () => {
  const { home } = makeHome();
  const { app, page } = await launch({ KB_HOME: home });
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
  const running = () => {
    try {
      return execFileSync("pgrep", ["-f", "--", `--kb-home ${home}`]).toString().trim(); // no shell: pgrep skips itself
    } catch {
      return "";
    }
  };
  expect(running()).not.toBe("");
  await app.close();
  await new Promise((r) => setTimeout(r, 1500));
  expect(running()).toBe("");
});
