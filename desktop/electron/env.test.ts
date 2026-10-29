import { engineEnv } from "./env";

test("macOS app started from Finder still finds claude", () => {
  const env = engineEnv({ PATH: "/usr/bin:/bin", HOME: "/Users/a" }, { platform: "darwin", home: "/Users/a", shellPath: "/opt/homebrew/bin:/usr/bin:/Users/a/.npm-global/bin" });
  const parts = env.PATH!.split(":");
  for (const p of ["/opt/homebrew/bin", "/Users/a/.npm-global/bin", "/Users/a/.local/bin", "/Users/a/.claude/local", "/usr/local/bin", "/usr/bin", "/bin"]) expect(parts).toContain(p);
  expect(parts.indexOf("/usr/bin")).toBe(parts.lastIndexOf("/usr/bin"));
  expect(env.HOME).toBe("/Users/a");
});

test("windows keeps its PATH", () => {
  expect(engineEnv({ PATH: "C:\\x" }, { platform: "win32", home: "C:\\u", shellPath: null }).PATH).toBe("C:\\x");
});
