import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";

/** The login shell's PATH (apps opened from Finder/Dock get only /usr/bin:/bin:/usr/sbin:/sbin). */
export function loginShellPath(): string | null {
  if (process.platform === "win32") return null;
  try {
    const shell = process.env.SHELL || "/bin/zsh";
    const out = execFileSync(shell, ["-ilc", "printf '__KBPATH__%s' \"$PATH\""], { encoding: "utf8", timeout: 5000 });
    return out.split("__KBPATH__").pop()!.trim() || null;
  } catch {
    return null;
  }
}

/** Engine environment whose PATH also covers where Claude Code and Node tools usually live. */
export function engineEnv(env: NodeJS.ProcessEnv, o: { platform: string; home: string; shellPath: string | null }): NodeJS.ProcessEnv {
  if (o.platform === "win32") return { ...env };
  const extra = [path.posix.join(o.home, ".local/bin"), path.posix.join(o.home, ".claude/local"), path.posix.join(o.home, ".npm-global/bin"),
    "/opt/homebrew/bin", "/usr/local/bin"];
  const parts = [...(o.shellPath ?? "").split(":"), ...(env.PATH ?? "").split(":"), ...extra].filter(Boolean);
  return { ...env, PATH: [...new Set(parts)].join(":") };
}

export function defaultEngineEnv(): NodeJS.ProcessEnv {
  return engineEnv(process.env, { platform: process.platform, home: os.homedir(), shellPath: loginShellPath() });
}
