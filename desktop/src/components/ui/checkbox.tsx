import * as CheckboxPrimitive from "@radix-ui/react-checkbox";
import * as React from "react";
import { cn } from "@/lib";

export function Checkbox({ className, ...props }: React.ComponentPropsWithoutRef<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      className={cn("flex h-[18px] w-[18px] items-center justify-center rounded border border-muted bg-surface data-[state=checked]:border-accent data-[state=checked]:bg-accent", className)}
      {...props}
    >
      <CheckboxPrimitive.Indicator>
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="3" aria-hidden="true"><path d="M5 12l5 5L20 7" /></svg>
      </CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
