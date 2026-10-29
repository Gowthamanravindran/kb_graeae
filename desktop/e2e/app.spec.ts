import { expect, test } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { launch, makeHome, makePrHome, startRegistry } from "./fixtures";

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


test("pr_test_plan_journey", async () => {
  test.setTimeout(240_000);
  const shots = path.join(__dirname, "screenshots");
  fs.mkdirSync(shots, { recursive: true });
  const { dir, home } = makePrHome();
  const saveDir = path.join(dir, "saved");
  fs.mkdirSync(saveDir);
  const { app, page } = await launch({ KB_HOME: home, KB_E2E_SAVE_DIR: saveDir });
  try {
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("link", { name: "Test plans" }).click();
    await page.getByLabel("mini-fe base branch").fill("master");
    await page.getByLabel("mini-be base branch").fill("develop");
    await page.getByLabel("mini-fe PR branch").fill("feature/repeat-end");
    await page.getByLabel("mini-be PR branch").fill("feature/repeat-end");
    await expect(page.getByText(/Base analysis found: production/)).toBeVisible();
    await page.screenshot({ path: path.join(shots, "5-testplans.png") });
    await page.getByRole("button", { name: "Find what changed" }).click();

    await expect(page.getByText("decision points").first()).toBeVisible({ timeout: 90_000 });
    await expect(page.getByText("Screen /tasks/create-task")).toBeVisible();
    await page.getByText("5 changed decision points").click();
    await page.screenshot({ path: path.join(shots, "6-impact.png") });
    for (const p of ["P0", "P1", "P2", "P3"]) await page.getByLabel(`${p} cases to generate now`).fill(p === "P0" ? "2" : "0");
    await expect(page.getByText("Will generate 2 of")).toBeVisible();
    await page.getByRole("button", { name: "Generate", exact: true }).click();

    const p0 = page.locator("div", { has: page.getByText("P0", { exact: true }) }).filter({ hasText: "/ 4" }).last();
    await expect(p0.getByText("2 / 4")).toBeVisible({ timeout: 90_000 });
    await page.screenshot({ path: path.join(shots, "7-board.png") });
    await page.getByRole("button", { name: "Export CSV" }).click();
    await expect(page.getByText(/^Saved /)).toBeVisible({ timeout: 15_000 });
    const csv = fs.readFileSync(path.join(saveDir, fs.readdirSync(saveDir)[0]), "utf8");
    expect(csv.split("TC-0").length - 1).toBe(2);

    await page.getByRole("button", { name: "Generate more" }).click();
    await page.getByLabel("P0 cases to generate now").fill("0");
    await page.getByLabel("P1 cases to generate now").fill("3");
    await page.getByRole("button", { name: "Generate", exact: true }).click();
    await expect(page.getByText("3 / 3").first()).toBeVisible({ timeout: 90_000 });
  } finally {
    await app.close();
  }
});

test("scenario_test_plan_journey", async () => {
  test.setTimeout(240_000);
  const { home } = makePrHome();
  const { app, page } = await launch({ KB_HOME: home });
  try {
    await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
    await page.getByRole("link", { name: "Test plans" }).click();
    await page.getByRole("tab", { name: /Test Scenarios in a Branch/ }).click();
    await page.getByLabel("Scenario", { exact: true }).fill("How do admins create repeating tasks?");
    await page.getByRole("button", { name: "Plan test cases" }).click();
    await expect(page.getByRole("button", { name: "Generate more" })).toBeVisible({ timeout: 90_000 });
    await page.getByRole("button", { name: "Generate more" }).click();
    for (const p of ["P0", "P1", "P2", "P3"]) await page.getByLabel(`${p} cases to generate now`).fill(p === "P0" ? "1" : "0");
    await page.getByRole("button", { name: "Generate", exact: true }).click();
    await expect(page.getByText("Written · ").first()).toBeVisible({ timeout: 90_000 });
  } finally {
    await app.close();
  }
});

test("add_project_from_repo_folders", async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kbadd-"));
  const home = path.join(dir, "home");
  const mk = (name: string, deps: Record<string, string>, branch: string) => {
    const d = path.join(dir, name);
    fs.mkdirSync(path.join(d, "src"), { recursive: true });
    fs.writeFileSync(path.join(d, "package.json"), JSON.stringify({ dependencies: deps }));
    fs.writeFileSync(path.join(d, "src", "index.ts"), "export const x = 1;\n");
    const g = (...a: string[]) => execFileSync("git", ["-c", "user.email=t@x", "-c", "user.name=t", ...a], { cwd: d });
    g("init", "-q", "-b", branch); g("add", "-A"); g("commit", "-qm", "init");
    return d;
  };
  const web = mk("shop-web", { react: "18", "@apollo/client": "3", "react-router-dom": "6" }, "master");
  const api = mk("shop-api-main", { "apollo-server": "3", bull: "4" }, "develop");
  const { app, page } = await launch({ KB_HOME: home });
  await expect(page.getByRole("heading", { name: "Library" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("main").getByRole("button", { name: "Add a project" }).click();
  await page.getByLabel("Project name").fill("shop");
  await page.getByLabel("Repo 1", { exact: true }).fill(web);
  await page.getByRole("button", { name: "Add another repo" }).click();
  await page.getByLabel("Repo 2", { exact: true }).fill(api);
  await page.getByRole("button", { name: "Read repos" }).click();
  await expect(page.getByLabel("Name of repo 2")).toHaveValue("shop-api", { timeout: 30_000 });
  await expect(page.getByLabel("Default branch of repo 2")).toHaveValue("develop");
  await expect(page.getByRole("checkbox", { name: "GraphQL: shop-web calls shop-api" })).toBeChecked();
  await page.getByRole("button", { name: "Create project" }).click();
  await expect(page.getByRole("dialog")).toBeHidden();
  const yaml = fs.readFileSync(path.join(home, "projects", "shop", "kb.yaml"), "utf8");
  expect(yaml).toContain("default_branch: develop");
  expect(yaml).toContain("type: bull-queue");
  await expect(page.getByText("shop", { exact: true }).first()).toBeVisible();
  await app.close();
});
