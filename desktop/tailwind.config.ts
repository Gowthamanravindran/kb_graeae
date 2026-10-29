import type { Config } from "tailwindcss";

// Colours from the approved mocks.
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: "#F6F6F4", surface: "#FFFFFF", line: "#E3E3DF", ink: "#18191C", muted: "#5E6168",
        accent: "#2F5BD3", "accent-ink": "#1F3F99", "accent-soft": "#E8EEFC",
        ok: "#1F7A4D", "ok-soft": "#E6F4EC", warn: "#8A5200", "warn-soft": "#FFF4E0", priv: "#3F4247", "priv-soft": "#EEEEEB",
      },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
} satisfies Config;
