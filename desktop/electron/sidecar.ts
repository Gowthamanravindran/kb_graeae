import { spawn, type ChildProcess } from "node:child_process";
import { randomBytes } from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export interface Sidecar { port: number; token: string; proc: ChildProcess; logPath: string }

export class SidecarError extends Error {
  constructor(message: string, public logTail: string, public logPath: string) {
    super(message);
  }
}

function tail(file: string, lines = 40): string {
  try {
    return fs.readFileSync(file, "utf8").split("\n").slice(-lines).join("\n");
  } catch {
    return "";
  }
}

/** Starts `kb serve`, resolving once it prints `KB_SERVE_READY {"port": N}`. */
export function startSidecar(o: { command: string; args: string[]; env: NodeJS.ProcessEnv; logDir: string; timeoutMs?: number }): Promise<Sidecar> {
  fs.mkdirSync(o.logDir, { recursive: true });
  const logPath = path.join(o.logDir, "kb-serve.log");
  const log = fs.createWriteStream(logPath, { flags: "a" });
  const token = randomBytes(32).toString("hex");
  log.write(`\n--- start ${new Date().toISOString()}: ${o.command} ${o.args.join(" ")}\n`);
  return new Promise((resolve, reject) => {
    let proc: ChildProcess;
    try {
      proc = spawn(o.command, o.args, { env: { ...o.env, KB_SERVE_TOKEN: token }, stdio: ["pipe", "pipe", "pipe"] /* stdin stays open: the engine exits when we die */ });
    } catch (e) {
      reject(new SidecarError(`could not start the engine: ${(e as Error).message}`, tail(logPath), logPath));
      return;
    }
    proc.stdin?.on("error", () => {}); // EPIPE when the engine dies; must not become an uncaught main-process error
    let settled = false;
    let buf = "";
    const fail = (msg: string) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      log.end(() => reject(new SidecarError(msg, tail(logPath), logPath)));
    };
    const timer = setTimeout(() => {
      proc.kill("SIGKILL");
      fail("the engine did not report ready in time");
    }, o.timeoutMs ?? 30000);
    proc.stdout!.on("data", (chunk: Buffer) => {
      log.write(chunk);
      buf += chunk.toString();
      const m = buf.match(/KB_SERVE_READY (\{.*\})/);
      if (m && !settled) {
        settled = true;
        clearTimeout(timer);
        resolve({ port: JSON.parse(m[1]).port, token, proc, logPath });
      }
    });
    proc.stderr!.on("data", (chunk: Buffer) => log.write(chunk));
    proc.on("error", (e) => fail(`could not start the engine: ${e.message}`));
    proc.on("exit", (code) => fail(`the engine exited (code ${code}) before it was ready`));
  });
}

export async function stopSidecar(proc: ChildProcess | null): Promise<void> {
  if (!proc || proc.exitCode !== null) return;
  proc.kill("SIGTERM");
  await new Promise<void>((done) => {
    const t = setTimeout(() => {
      proc.kill("SIGKILL");
      done();
    }, 3000);
    proc.once("exit", () => {
      clearTimeout(t);
      done();
    });
  });
}
