import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { launch, makeHome, startRegistry } from "./fixtures";

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

test("engine_dying_later_shows_error_screen", async () => {
  const { home } = makeHome();
  const { app, page } = await launch({ KB_HOME: home });
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
  try {
    execFileSync("pkill", ["-KILL", "-f", "--", `--kb-home ${home}`]);
  } catch {
    /* pkill exits 1 if nothing matched; the expectation below then fails */
  }
  await expect(page.getByRole("heading", { name: "KB engine stopped" })).toBeVisible({ timeout: 20_000 });
  await page.getByRole("button", { name: "Retry" }).click();
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
  await app.close();
});

test("full_journey", async () => {
  test.setTimeout(240_000);
  const shots = path.join(__dirname, "screenshots");
  fs.mkdirSync(shots, { recursive: true });
  const regDir = fs.mkdtempSync(path.join(os.tmpdir(), "kbreg-"));
  const registry = await startRegistry(regDir);
  const { home } = makeHome(registry.url);
  const { app, page } = await launch({ KB_HOME: home });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
    const main = page.locator("tr", { has: page.getByText("main", { exact: true }) });
    await expect(main.getByText("Private · local only")).toBeVisible();

    await page.getByRole("button", { name: "Sign in to registry" }).click();
    await page.getByLabel("API token from your registry admin").fill(registry.token);
    await page.getByRole("button", { name: "Sign in", exact: true }).click();
    await expect(page.getByRole("button", { name: "Sign in to registry" })).toHaveCount(0, { timeout: 15_000 });

    await main.getByRole("button", { name: "Push to registry" }).click();
    await expect(main.getByText("Synced")).toBeVisible({ timeout: 60_000 });
    await page.screenshot({ path: path.join(shots, "1-library.png") });

    await main.getByRole("button", { name: "Open" }).click();
    await expect(page.getByRole("heading", { name: "How mini is put together" })).toBeVisible();
    await page.getByRole("button", { name: /^Tasks/ }).click();
    await page.screenshot({ path: path.join(shots, "2-map.png") });
    await page.getByRole("button", { name: "Ask about Tasks" }).click();

    await expect(page.getByLabel("Question")).toHaveValue("How does Tasks work?");
    await page.getByRole("button", { name: "Ask", exact: true }).click();
    await expect(page.getByText(/^(\d+) of \1 steps verified$/)).toBeVisible({ timeout: 90_000 });
    await expect(page.locator(".kb-diagram svg").first()).toBeVisible({ timeout: 15_000 });
    await page.screenshot({ path: path.join(shots, "3-ask.png") });
    await page.getByRole("button", { name: "Share to mini@main" }).click();
    await expect(page.getByText("Shared", { exact: true })).toBeVisible({ timeout: 30_000 });

    await page.getByRole("link", { name: "Memory" }).or(page.getByRole("button", { name: "Memory" })).first().click();
    const item = page.locator("li", { has: page.getByText("How does Tasks work?") });
    await expect(item.getByText("Published")).toBeVisible({ timeout: 30_000 });
    await page.screenshot({ path: path.join(shots, "4-memory.png") });
  } finally {
    await app.close();
    registry.stop();
  }
});
