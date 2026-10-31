import { Background, Controls, Handle, MarkerType, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import type { ConnEdge, ConnNode, Connections, Loc, MapModule } from "../api/types";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Spinner } from "../components/ui/spinner";
import { cn } from "../lib";
import { useApp } from "../state";

const W = 240, H = 48, GAP = 10, LANE_GAP = 260, HEAD = 52;
const KIND: Record<string, { label: string; color: string }> = {
  graphql: { label: "GraphQL", color: "#2F5BD3" },
  http: { label: "REST", color: "#1B7F5C" },
  queue: { label: "Queue", color: "#B26A00" },
  kafka: { label: "Kafka", color: "#8A3FB8" },
  sqs: { label: "SQS", color: "#C2410C" },
  rabbitmq: { label: "RabbitMQ", color: "#B4235A" },
};
const kindOf = (k: string) => KIND[k] ?? { label: k, color: "#5E6168" };
const UNRESOLVED: Record<string, [string, string]> = {
  graphql: ["GraphQL call", "GraphQL calls"], http: ["REST call", "REST calls"], queue: ["queued job", "queued jobs"],
  message: ["message", "messages"], route: ["route", "routes"],
};

type BoxData = { node: ConnNode; lane: string; selected: boolean; dim: boolean; onSelect(id: string): void };
type LaneData = { label: string; role: string };

function Box({ data }: NodeProps<Node<BoxData>>) {
  const { node, lane, selected, dim, onSelect } = data;
  return (
    <>
    <Handle type="target" position={Position.Left} className="!h-1.5 !w-1.5 !min-h-0 !min-w-0 !border-0 !bg-line" isConnectable={false} />
    <button type="button" aria-label={`${node.name} · ${lane}`} onClick={() => onSelect(node.id)}
      className={cn("nodrag nopan flex h-[48px] w-[240px] flex-col justify-center rounded-lg border bg-surface px-3 text-left shadow-sm transition-opacity hover:border-accent",
        node.kind === "jobs" && "border-dashed", selected ? "border-2 border-accent" : "border-line", dim && "opacity-40")}>
      <span className="truncate text-sm font-semibold">{node.name}</span>
      <span className="truncate font-mono text-[11px] text-muted">{node.code_name}{node.routes.length ? ` · ${node.routes.length} route${node.routes.length === 1 ? "" : "s"}` : ""}</span>
    </button>
    <Handle type="source" position={Position.Right} className="!h-1.5 !w-1.5 !min-h-0 !min-w-0 !border-0 !bg-line" isConnectable={false} />
    </>
  );
}
function Lane({ data }: NodeProps<Node<LaneData>>) {
  return (
    <div className="w-[240px] rounded-lg bg-accent-soft px-3 py-2 text-sm font-semibold text-accent-ink">
      {data.label} <span className="font-normal opacity-70">· {data.role === "jobs" ? "queues and workers" : data.role}</span>
    </div>
  );
}
const nodeTypes = { box: Box, lane: Lane };

export function graphLayout(g: Connections, selected: string | null, edgeSel: string | null, onSelect: (id: string) => void) {
  const touching = new Set<string>();
  for (const e of g.edges) {
    if (e.id === edgeSel || (selected && (e.source === selected || e.target === selected))) { touching.add(e.source); touching.add(e.target); }
  }
  const focus = selected !== null || edgeSel !== null;
  const nodes: Node[] = [];
  const laneLabel = Object.fromEntries(g.lanes.map((l) => [l.id, l.label]));
  // Three columns by role (apps | services | background jobs); each repo is a labelled group stacked inside
  // its column. Lines only ever cross the gap between columns, so they never run behind another repo's boxes,
  // however many apps or services there are.
  const colOf = (role: string) => (role === "jobs" ? 2 : ["frontend", "web", "mobile"].includes(role) ? 0 : 1);
  const used = [...new Set(g.lanes.map((l) => colOf(l.role)))].sort();
  const y = [0, 0, 0];
  for (const l of g.lanes) {
    const col = colOf(l.role);
    const x = used.indexOf(col) * (W + LANE_GAP);
    nodes.push({ id: `lane-${l.id}`, type: "lane", position: { x, y: y[col] }, data: { label: l.label, role: l.role }, draggable: false, selectable: false, width: W, height: 40 });
    const mine = g.nodes.filter((n) => n.lane === l.id);
    mine.forEach((n, i) => nodes.push({
      id: n.id, type: "box", draggable: false, width: W, height: H, position: { x, y: y[col] + HEAD + i * (H + GAP) },
      data: { node: n, lane: laneLabel[n.lane] ?? n.lane, selected: n.id === selected, dim: focus && !touching.has(n.id), onSelect },
    }));
    y[col] += HEAD + mine.length * (H + GAP) + 36;
  }
  const max = Math.max(1, ...g.edges.map((e) => e.count));
  const edges: Edge[] = g.edges.map((e) => {
    const on = e.id === edgeSel || (!!selected && (e.source === selected || e.target === selected));
    const { color, label } = kindOf(e.kind);
    return {
      id: e.id, source: e.source, target: e.target, animated: on && e.kind === "queue",
      markerEnd: { type: MarkerType.ArrowClosed, color },
      style: { stroke: color, strokeWidth: 1 + (3 * e.count) / max, opacity: focus ? (on ? 1 : 0.06) : 0.35 },
      label: on ? `${label} · ${e.count}` : undefined, labelStyle: { fontSize: 11, fontWeight: 600 },
    };
  });
  return { nodes, edges };
}

function Cite({ loc, project, branch }: { loc: Loc | null; project: string; branch: string }) {
  if (!loc) return <span className="text-muted">—</span>;
  const text = `${loc.file}:${loc.line}`;
  return (
    <button type="button" className="truncate text-left font-mono text-[11px] text-accent hover:underline" title={`${loc.repo}: ${text}`}
      onClick={() => void window.kb?.openCitation({ project, branch, repo: loc.repo, file: loc.file, line: loc.line })}>{text}</button>
  );
}

export function ConnectionsGraph({ modules }: { modules: MapModule[] }) {
  const { api, project, branch, go } = useApp();
  const [g, setG] = useState<Connections | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [edgeSel, setEdgeSel] = useState<string | null>(null);
  useEffect(() => {
    if (!project || !branch) return;
    setG(null);
    api.connections(project, branch).then(setG, (e) => setError(e instanceof ApiError ? e.detail : String(e)));
  }, [api, project, branch]);
  const pick = (id: string) => { setSelected(id); setEdgeSel(null); };
  const { nodes, edges } = useMemo(() => (g ? graphLayout(g, selected, edgeSel, pick) : { nodes: [], edges: [] }), [g, selected, edgeSel]);
  if (error) return <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>;
  if (!g) return <p className="flex items-center gap-2 text-muted"><Spinner />Loading the connections…</p>;
  if (!g.nodes.length) {
    return (
      <Card className="p-6 text-sm text-muted">
        No links between the repos were found in this analysis. KB draws them from GraphQL calls, routes and queues; check
        that the project's links (GraphQL, React Router, Bull) are set, then analyse the branch again.
      </Card>
    );
  }
  const byId = Object.fromEntries(g.nodes.map((n) => [n.id, n]));
  const laneLabel = Object.fromEntries(g.lanes.map((l) => [l.id, l.label]));
  const node = selected ? byId[selected] : null;
  const edge = edgeSel ? g.edges.find((e) => e.id === edgeSel) ?? null : null;
  const mod = node?.module_id ? modules.find((m) => m.id === node.module_id) : undefined;
  const mine = node ? g.edges.filter((e) => e.source === node.id || e.target === node.id) : [];
  const other = (e: ConnEdge) => byId[e.source === node?.id ? e.target : e.source];
  const unresolved = Object.entries(g.unresolved).filter(([, n]) => n > 0);
  const present = new Set(g.edges.map((e) => e.kind));   // the legend lists only the kinds of link drawn
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-4 text-xs text-muted">
        <ul aria-label="Link types" className="flex flex-wrap items-center gap-4">
          {Object.entries(KIND).filter(([k]) => present.has(k)).map(([k, v]) => (
            <li key={k} className="flex items-center gap-1.5"><span className="inline-block h-0.5 w-5" style={{ background: v.color }} />{v.label}</li>
          ))}
        </ul>
        <span>Pick a box or a line to see the actual calls.</span>
      </div>
      <div className="flex flex-col gap-5 lg:flex-row">
        <Card className="h-[max(620px,75vh)] min-w-0 flex-1 overflow-hidden">
          <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.1} nodesConnectable={false}
            onEdgeClick={(_, e) => { setEdgeSel(e.id); setSelected(null); }}
            onPaneClick={() => { setSelected(null); setEdgeSel(null); }} proOptions={{ hideAttribution: true }}>
            <Background gap={24} />
            <Controls showInteractive={false} />
          </ReactFlow>
        </Card>
        <aside aria-label="Connection details" className="w-full shrink-0 lg:w-[340px]">
          <Card className="flex max-h-[max(620px,75vh)] flex-col gap-4 overflow-y-auto p-5">
            {edge ? (
              <>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted">{kindOf(edge.kind).label} · {edge.count}</div>
                  <h2 className="mt-1 text-base font-semibold">{byId[edge.source]?.name} <span className="text-muted">({laneLabel[byId[edge.source]?.lane]})</span> → {byId[edge.target]?.name} <span className="text-muted">({laneLabel[byId[edge.target]?.lane]})</span></h2>
                </div>
                <ul className="flex flex-col gap-2.5">
                  {edge.items.map((it, i) => (
                    <li key={i} className="flex flex-col gap-0.5 border-b border-line pb-2 last:border-0">
                      <span className="text-sm font-semibold">{it.label}</span>
                      <span className="flex items-center gap-1 text-[11px] text-muted"><span className="w-10 shrink-0">from</span><Cite loc={it.from} project={project!} branch={branch!} /></span>
                      <span className="flex items-center gap-1 text-[11px] text-muted"><span className="w-10 shrink-0">to</span><Cite loc={it.to} project={project!} branch={branch!} /></span>
                    </li>
                  ))}
                </ul>
                {edge.count > edge.items.length && <p className="text-xs text-muted">Showing {edge.items.length} of {edge.count}.</p>}
                <Button variant="secondary" onClick={() => go("ask", { askSeed: { question: `How does ${byId[edge.source]?.name} in ${laneLabel[byId[edge.source]?.lane]} use ${byId[edge.target]?.name} in ${laneLabel[byId[edge.target]?.lane]}?` } })}>Ask how these connect</Button>
              </>
            ) : node ? (
              <>
                <div>
                  <div className="text-xs uppercase tracking-wide text-muted">{laneLabel[node.lane] ?? node.lane}</div>
                  <h2 className="mt-1 text-lg font-semibold">{node.name}</h2>
                  {mod && <p className="mt-1.5 text-sm text-muted">{mod.summary}</p>}
                </div>
                {!!node.routes.length && (
                  <div>
                    <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Opened by routes</div>
                    <ul className="flex flex-col gap-1">{node.routes.map((r) => <li key={r.path + r.file} className="font-mono text-xs">{r.path}</li>)}</ul>
                  </div>
                )}
                <div>
                  <div className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted">Connects to</div>
                  <ul className="flex flex-col gap-1">
                    {mine.map((e) => (
                      <li key={e.id}>
                        <button type="button" className="text-left text-sm text-accent hover:underline" onClick={() => setEdgeSel(e.id)}>
                          {kindOf(e.kind).label} {e.source === node.id ? "→" : "←"} {other(e)?.name} ({laneLabel[other(e)?.lane] ?? ""}) · {e.count}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
                {mod && <Button variant="secondary" onClick={() => go("ask", { askSeed: { question: `How does ${mod.name} work?` } })}>Ask about {mod.name}</Button>}
              </>
            ) : (
              <p className="text-sm text-muted">Apps on the left, services in the middle, background jobs on the right; each repo is a labelled group. Lines show the GraphQL calls from apps to services and the jobs one part queues for another. Pick a box or a line.</p>
            )}
          </Card>
        </aside>
      </div>
      {!!unresolved.length && (
        <p className="text-xs text-muted">
          {unresolved.map(([k, n]) => `${n} ${(UNRESOLVED[k] ?? [k, k])[n === 1 ? 0 : 1]}`).join(", ")} couldn't be matched to the other side, so they aren't drawn.
        </p>
      )}
    </div>
  );
}
