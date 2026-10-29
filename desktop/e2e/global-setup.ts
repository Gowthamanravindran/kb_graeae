import { execSync } from "node:child_process";

export default function globalSetup() {
  execSync("npm run build", { cwd: `${__dirname}/..`, stdio: "inherit" });
}
