import mermaid from "mermaid";
import { useEffect, useState } from "react";

let seq = 0;
let ready = false;

/** Renders a Mermaid diagram (strict security). Falls back to showing the source on error. */
export function Mermaid({ code, className }: { code: string; className?: string }) {
  const [svg, setSvg] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    setSvg(null);
    setFailed(false);
    if (!ready) {
      mermaid.initialize({ startOnLoad: false, securityLevel: "strict", theme: "neutral", fontFamily: "inherit" });
      ready = true;
    }
    mermaid
      .render(`kb-mermaid-${++seq}`, code)
      .then((r: { svg: string }) => live && setSvg(r.svg))
      .catch(() => live && setFailed(true));
    return () => {
      live = false;
    };
  }, [code]);
  if (failed) return <pre className="overflow-x-auto rounded-lg bg-bg p-3 font-mono text-xs text-muted">{code}</pre>;
  if (!svg) return <div className={`h-24 animate-pulse rounded-lg bg-bg ${className ?? ""}`} aria-label="Drawing diagram" />;
  return <div className={`kb-diagram overflow-x-auto ${className ?? ""}`} dangerouslySetInnerHTML={{ __html: svg }} />;
}
