import { _electron as electron, type ElectronApplication, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const repo = path.resolve(__dirname, "..", "..");

/** A temp KB_HOME holding the mini pack and a desktop.json that lists it. */
export function makeHome(registry?: string): { dir: string; home: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kbe2e-"));
  const out = execFileSync("uv", ["run", "--project", repo, "python", path.join(repo, "tests/e2e_support/make_home.py"), dir, ...(registry ? [registry] : [])],
    { cwd: repo, encoding: "utf8" });
  return { dir, home: out.trim().split("\n").pop()! };
}

/** A KB_HOME with the stage 5a fixture repos (git history + PR branch) and `production` analysed. */
export function makePrHome(): { dir: string; home: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kbpr-"));
  const out = execFileSync("uv", ["run", "--project", repo, "python", path.join(repo, "tests/e2e_support/make_pr_home.py"), dir],
    { cwd: repo, encoding: "utf8" });
  return { dir, home: out.trim().split("\n").pop()! };
}

export async function launch(env: Record<string, string>): Promise<{ app: ElectronApplication; page: Page }> {
  const app = await electron.launch({
    args: ["--no-sandbox", path.join(__dirname, "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      PYTHONPATH: [path.join(repo, "tests/e2e_support"), path.join(repo, "tests"), path.join(repo, "tests/testplan")].join(":"),
      KB_SERVE_PROVIDER: "e2e_fakes:make_provider",
      // remembered screens etc. stay with this test's home, so one test can't reopen another's plan
      ...(env.KB_HOME ? { KB_USER_DATA: path.join(env.KB_HOME, ".electron-user-data") } : {}),
      ...env,
    } as Record<string, string>,
  });
  const page = await app.firstWindow();
  return { app, page };
}

/** A real kb-registry (SQLite + file store) on a free port, with project `mini` and a maintainer token. */
export async function startRegistry(dir: string): Promise<{ url: string; token: string; contribToken: string; stop(): void }> {
  const { spawn } = await import("node:child_process");
  const net = await import("node:net");
  const port = await new Promise<number>((resolve) => {
    const s = net.createServer().listen(0, "127.0.0.1", () => {
      const p = (s.address() as { port: number }).port;
      s.close(() => resolve(p));
    });
  });
  const env = { ...process.env, KB_REGISTRY_DATABASE_URL: `sqlite:///${dir}/registry.db`, KB_REGISTRY_STORAGE: `file://${dir}/blobs` };
  const reg = (...args: string[]) => execFileSync("uv", ["run", "--project", repo, "kb-registry", ...args], { cwd: dir, env, encoding: "utf8" });
  reg("migrate");
  reg("admin", "create-project", "mini");
  const token = reg("admin", "create-user", "maint@x").trim().split("\n").pop()!.trim();
  reg("admin", "grant", "maint@x", "mini", "maintainer");
  const contribToken = reg("admin", "create-user", "contrib@x").trim().split("\n").pop()!.trim();
  reg("admin", "grant", "contrib@x", "mini", "contributor");
  const proc = spawn("uv", ["run", "--project", repo, "kb-registry", "serve", "--port", String(port)], { cwd: dir, env, stdio: "ignore" });
  const url = `http://127.0.0.1:${port}`;
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`${url}/api/v1/auth/config`)).ok) break;
    } catch {
      /* not up yet */
    }
    await new Promise((r) => setTimeout(r, 250));
  }
  return { url, token, contribToken, stop: () => proc.kill() };
}


/** Two QAs on one shared project (see tests/e2e_support/make_team_homes.py). */
export function makeTeamHomes(url: string, maint: string, contrib: string): { homeA: string; homeB: string; planId: string } {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "kbteam-"));
  const out = execFileSync("uv", ["run", "--project", repo, "python", path.join(repo, "tests/e2e_support/make_team_homes.py"), dir, url, maint, contrib],
    { cwd: repo, encoding: "utf8" }).trim().split("\n");
  const [homeA, homeB, planId] = out.slice(-3);
  return { homeA, homeB, planId };
}
