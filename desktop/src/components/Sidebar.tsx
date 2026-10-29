import { useApp, type View } from "../state";
import { ProjectSwitcher } from "./ProjectSwitcher";
import { StatusFooter } from "./StatusFooter";

const NAV: { view: View; label: string }[] = [
  { view: "library", label: "Library" }, { view: "map", label: "Map" }, { view: "ask", label: "Ask" }, { view: "memory", label: "Memory" },
  { view: "testplans", label: "Test plans" },
  { view: "settings", label: "Settings" },
];

export function Sidebar() {
  const { view, go } = useApp();
  return (
    <nav aria-label="Main" className="flex w-[240px] shrink-0 flex-col gap-6 border-r border-line bg-surface px-4 py-5">
      <div className="flex items-center gap-2.5">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-ink text-[13px] font-bold text-white">kb</div>
        <div className="text-[15px] font-semibold">KB Desktop</div>
      </div>
      <ProjectSwitcher />
      <div className="flex flex-col gap-1">
        {NAV.map((n) => (
          <a key={n.view} href={`#${n.view}`} aria-current={view === n.view ? "page" : undefined}
            onClick={(e) => { e.preventDefault(); go(n.view, n.view === "testplans" ? { planScreen: "list" } : {}); }}
            className={`rounded-lg px-3 py-2.5 no-underline ${view === n.view ? "bg-accent-soft font-semibold text-accent-ink" : "text-ink hover:bg-bg"}`}>
            {n.label}
          </a>
        ))}
      </div>
      <StatusFooter />
    </nav>
  );
}
