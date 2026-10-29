/** A small turning ring: shown wherever KB is busy for more than a moment. */
export function Spinner({ className = "" }: { className?: string }) {
  return <span role="status" aria-label="Working" className={`inline-block h-3.5 w-3.5 shrink-0 animate-spin rounded-full border-2 border-accent border-t-transparent ${className}`} />;
}
