import { Background, Controls, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ApiError } from "../api/client";
import type { MapModule, MapView as MapPayload } from "../api/types";
import { Mermaid } from "../components/Mermaid";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { cn } from "../lib";
import { useApp } from "../state";

const W = 230, H = 96, GAP = 24, COLS = 4, BAND = 40;

type ModData = { mod: MapModule; codeNames: boolean; selected: boolean; onSelect(id: string): void };
type AppData = { label: string; role: string };
type BandData = { label: string };

function ModuleNode({ data }: NodeProps<Node<ModData>>) {
  const { mod, codeNames, selected, onSelect } = data;
  return (
    <button
      type="button"
      onClick={() => onSelect(mod.id)}
      className={cn(
        "nodrag nopan flex h-[96px] w-[230px] flex-col gap-1 rounded-xl border bg-surface p-3 text-left shadow-sm transition-colors hover:border-accent",
        mod.layer === "behind" && "border-dashed",
        selected ? "border-2 border-accent" : "border-line",
      )}
    >
      <span className="truncate text-sm font-semibold">{codeNames ? mod.id : mod.name}</span>
      <span className="line-clamp-2 text-xs text-muted">{codeNames ? mod.top_paths.slice(0, 2).join(", ") : mod.summary}</span>
    </button>
  );
}
function AppNode({ data }: NodeProps<Node<AppData>>) {
  return <div className="w-[230px] rounded-xl bg-accent-soft px-4 py-3 text-sm font-semibold text-accent-ink">{data.label} <span className="font-normal opacity-70">· {data.role}</span></div>;
}
function BandNode({ data }: NodeProps<Node<BandData>>) {
  return <div className="text-xs font-semibold uppercase tracking-wide text-muted">{data.label}</div>;
}
const nodeTypes = { module: ModuleNode, app: AppNode, band: BandNode };

export function layout(map: MapPayload, codeNames: boolean, selected: string | null, onSelect: (id: string) => void) {
  const nodes: Node[] = [];
  let y = 0;
  const band = (id: string, label: string) => {
    nodes.push({ id, type: "band", position: { x: 0, y }, data: { label }, selectable: false, draggable: false, width: W, height: 20 });
    y += BAND;
  };
  const grid = (mods: MapModule[]) => {
    mods.forEach((mod, i) => nodes.push({
      id: mod.id, type: "module", draggable: false, width: W, height: H,
      position: { x: (i % COLS) * (W + GAP), y: y + Math.floor(i / COLS) * (H + GAP) },
      data: { mod, codeNames, selected: mod.id === selected, onSelect },
    }));
    y += Math.ceil(mods.length / COLS) * (H + GAP) + GAP;
  };
  if (map.apps.length) {
    band("band-apps", "Where people use it");
    map.apps.forEach((a, i) => nodes.push({ id: `app-${a.repo}`, type: "app", draggable: false, width: W, height: 48, position: { x: i * (W + GAP), y }, data: { label: a.label, role: a.role } }));
    y += 60 + GAP;
  }
  const product = map.modules.filter((m) => m.layer === "product");
  const behind = map.modules.filter((m) => m.layer === "behind");
  if (product.length) { band("band-product", "Product modules"); grid(product); }
  if (behind.length) { band("band-behind", "Behind the scenes"); grid(behind); }
  const max = Math.max(1, ...map.edges.map((e) => e.count));
  const edges: Edge[] = map.edges.map((e) => ({
    id: `${e.source}->${e.target}`, source: e.source, target: e.target,
    hidden: !(selected && (e.source === selected || e.target === selected)),
    style: { strokeWidth: 1 + (3 * e.count) / max, stroke: "var(--kb-accent, #4F46E5)" },
    label: `${e.count}`,
  }));
  return { nodes, edges };
}

export function MapView() {
  const { api, project, branch, go } = useApp();
  const [map, setMap] = useState<MapPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [codeNames, setCodeNames] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [overview, setOverview] = useState(false);
  useEffect(() => {
    if (!project || !branch) return;
    setMap(null);
    setSelected(null);
    api.map(project, branch).then(setMap, (e) => setError(e instanceof ApiError ? e.detail : String(e)));
  }, [api, project, branch]);
  const { nodes, edges } = useMemo(
    () => (map ? layout(map, codeNames, selected, setSelected) : { nodes: [], edges: [] }),
    [map, codeNames, selected],
  );
  if (!project || !branch) return <p className="text-muted">Open a branch from the Library to see its map.</p>;
  if (error) return <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>;
  if (!map) return <p className="text-muted">Loading the map…</p>;
  const mod = map.modules.find((m) => m.id === selected) ?? null;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-bold tracking-tight">How {project} is put together</h1>
          <p className="mt-1.5 text-muted">{branch} · {map.modules.length} modules</p>
        </div>
        <div role="radiogroup" aria-label="Labels" className="flex rounded-lg border border-line bg-surface p-1">
          {[["Plain English", false], ["Code names", true]].map(([label, v]) => (
            <button key={String(label)} type="button" role="radio" aria-checked={codeNames === v} onClick={() => setCodeNames(v as boolean)}
              className={cn("rounded-md px-3 py-1.5 text-sm", codeNames === v ? "bg-ink text-white" : "text-muted")}>{label as string}</button>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-5 lg:flex-row">
        <Card className="h-[560px] min-w-0 flex-1 overflow-hidden">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.2} nodesConnectable={false}
            onPaneClick={() => setSelected(null)} proOptions={{ hideAttribution: true }}>
            <Background gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </Card>
        <aside aria-label="Module details" className="w-full shrink-0 lg:w-[320px]">
          <Card className="flex flex-col gap-4 p-5">
            {mod ? (
              <>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted">Module</div>
                  <h2 className="mt-1 text-lg font-semibold">{codeNames ? mod.id : mod.name}</h2>
                  <p className="mt-1.5 text-sm text-muted">{mod.summary}</p>
                </div>
                {!!mod.entry_points.length && (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Starts at</div>
                    <ul className="flex flex-col gap-1">
                      {mod.entry_points.map((e) => (
                        <li key={e.id} className="font-mono text-xs" title={e.name}>{e.file}{e.line ? `:${e.line}` : ""}</li>
                      ))}
                    </ul>
                  </div>
                )}
                {!!mod.answered.length && (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Already explained</div>
                    <ul className="flex flex-col gap-1">
                      {mod.answered.map((a) => (
                        <li key={a.qa_id}>
                          <button type="button" className="text-left text-sm text-accent hover:underline" onClick={() => go("ask", { askSeed: { qaId: a.qa_id } })}>{a.question}</button>
                        </li>
                      ))}
                    </ul>
                  </div>
                )}
                <Button onClick={() => go("ask", { askSeed: { question: `How does ${mod.name} work?` } })}>Ask about {mod.name}</Button>
              </>
            ) : (
              <p className="text-sm text-muted">Pick a module on the map to see what it does, where it starts, and what has already been explained.</p>
            )}
          </Card>
        </aside>
      </div>
      {map.system_md && (
        <Card className="p-5">
          <button type="button" aria-expanded={overview} className="flex w-full items-center justify-between text-left font-semibold" onClick={() => setOverview((o) => !o)}>
            System overview <span className="text-muted">{overview ? "−" : "+"}</span>
          </button>
          {overview && (
            <div className="prose-kb mt-4 flex flex-col gap-3 text-sm leading-relaxed">
              <ReactMarkdown
                components={{
                  code({ className, children }) {
                    const text = String(children).replace(/\n$/, "");
                    if (className?.includes("language-mermaid")) return <Mermaid code={text} />;
                    return <code className="font-mono text-xs">{children}</code>;
                  },
                  pre: ({ children }) => <>{children}</>,
                }}
              >
                {map.system_md}
              </ReactMarkdown>
            </div>
          )}
        </Card>
      )}
    </div>
  );
}
