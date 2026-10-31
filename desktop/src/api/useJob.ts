import { useEffect, useState } from "react";
import type { Api } from "./client";
import type { Job } from "./types";

const MAX_MISSES = 8;

/** Polls a server job until it is done or failed. Safe to remount with the same id (resumes polling). */
export function useJob<T = unknown>(api: Api, jobId: string | null, intervalMs = 700) {
  const [job, setJob] = useState<Job<T> | null>(null);
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let stopped = false;
    let misses = 0;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await api.job<T>(jobId);
        if (stopped) return;
        misses = 0;
        setJob(j);
        if (j.state === "done" || j.state === "failed") return;
      } catch (e) {
        if (stopped) return;
        const gone = (e as { status?: number }).status === 404;
        if (gone || ++misses >= MAX_MISSES) {
          setJob({ id: jobId, kind: "", key: null, state: "failed", step: "", progress: 0, result: null, created_at: "",
            error: gone ? "This job is gone (the Graeae engine restarted). Run it again." : "Lost contact with the Graeae engine. Restart the app if this keeps happening." });
          return;
        }
      }
      timer = setTimeout(tick, intervalMs);
    };
    tick();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [api, jobId, intervalMs]);
  return { job, done: job?.state === "done", failed: job?.state === "failed" };
}
