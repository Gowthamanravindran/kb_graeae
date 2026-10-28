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

export async function launch(env: Record<string, string>): Promise<{ app: ElectronApplication; page: Page }> {
  const app = await electron.launch({
    args: ["--no-sandbox", path.join(__dirname, "..", "dist-electron", "main.js")],
    env: {
      ...process.env,
      PYTHONPATH: [path.join(repo, "tests/e2e_support"), path.join(repo, "tests")].join(":"),
      KB_SERVE_PROVIDER: "e2e_fakes:make_provider",
      ...env,
    } as Record<string, string>,
  });
  const page = await app.firstWindow();
  return { app, page };
}
