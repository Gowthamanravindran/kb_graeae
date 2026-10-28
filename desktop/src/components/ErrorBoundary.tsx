import { Component, type ReactNode } from "react";
import { Button } from "./ui/button";

/** Catches render errors so a bad value shows a recovery screen instead of a blank window. */
export class ErrorBoundary extends Component<{ children: ReactNode; onReset?(): void }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (!this.state.error) return this.props.children;
    return (
      <div className="flex flex-col items-start gap-3 p-10">
        <h1 className="text-[22px] font-bold">Something went wrong</h1>
        <p className="text-muted">This screen hit an error and couldn't be shown.</p>
        <pre className="max-w-full overflow-x-auto rounded-lg bg-bg p-3 font-mono text-xs text-muted">{this.state.error.message}</pre>
        <Button onClick={() => { this.setState({ error: null }); this.props.onReset?.(); }}>Back to the Library</Button>
      </div>
    );
  }
}
