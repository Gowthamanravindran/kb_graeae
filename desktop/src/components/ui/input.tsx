import * as React from "react";
import { cn } from "@/lib";

export const Input = React.forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(({ className, ...props }, ref) => (
  <input ref={ref} className={cn("h-10 w-full rounded-lg border border-line bg-surface px-3 text-sm outline-none focus:ring-2 focus:ring-accent", className)} {...props} />
));
Input.displayName = "Input";
