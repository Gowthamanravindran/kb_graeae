import * as React from "react";
import { cn } from "@/lib";

const tones = {
  ok: "bg-ok-soft text-ok",
  warn: "bg-warn-soft text-warn",
  priv: "bg-priv-soft text-priv",
  accent: "bg-accent-soft text-accent-ink",
} as const;

export function Badge({ tone = "priv", className, ...props }: React.HTMLAttributes<HTMLSpanElement> & { tone?: keyof typeof tones }) {
  return <span className={cn("inline-flex items-center rounded-full px-2 py-0.5 text-xs font-semibold", tones[tone], className)} {...props} />;
}
