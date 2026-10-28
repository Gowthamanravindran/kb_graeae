import { useEffect, useState } from "react";
import type { RegistryStatus } from "../api/types";
import { useApp } from "../state";

function Dot({ on }: { on: boolean }) {
  return <span aria-hidden="true" className={`h-2 w-2 shrink-0 rounded-full ${on ? "bg-ok" : "bg-[#9A9DA3]"}`} />;
}

export function StatusFooter() {
  const { api, status, current } = useApp();
  const [reg, setReg] = useState<RegistryStatus | null>(null);
  useEffect(() => {
    setReg(null);
    if (current?.name && current.registry) void api.registry(current.name).then(setReg).catch(() => setReg(null));
  }, [api, current?.name, current?.registry]);
  const host = current?.registry ? new URL(current.registry).host : null;
  const claudeOk = !!status?.claude.available;
  return (
    <div className="mt-auto flex flex-col gap-2 rounded-lg bg-bg p-3 text-xs text-muted">
      <div className="flex items-center gap-2" title={reg?.email ? `signed in as ${reg.email}` : reg && !reg.reachable ? "registry unreachable" : ""}>
        <Dot on={!!reg?.logged_in && !!reg?.reachable} />
        {host ? `Registry · ${host}` : "Registry · not set"}
      </div>
      <div className="flex items-center gap-2" title={status?.claude.reason ?? ""}>
        <Dot on={claudeOk} />
        <span>{claudeOk ? "Claude Code · signed in" : "Claude Code · not available"}</span>
      </div>
      <div className="flex items-center gap-2">
        <Dot on={!!status?.langfuse.configured} />
        {status?.langfuse.configured ? "Langfuse · configured" : "Langfuse · off"}
      </div>
    </div>
  );
}
