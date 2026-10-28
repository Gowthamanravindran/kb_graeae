import { useEffect, useState } from "react";
import type { Api } from "./client";
import type { Job } from "./types";

/** Polls a server job until it is done or failed. Safe to remount with the same id (resumes polling). */
export function useJob<T = unknown>(api: Api, jobId: string | null, intervalMs = 700) {
  const [job, setJob] = useState<Job<T> | null>(null);
  useEffect(() => {
    if (!jobId) {
      setJob(null);
      return;
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      try {
        const j = await api.job<T>(jobId);
        if (stopped) return;
        setJob(j);
        if (j.state === "done" || j.state === "failed") return;
      } catch {
        if (stopped) return;
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
