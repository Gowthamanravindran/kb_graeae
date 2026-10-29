import ReactMarkdown from "react-markdown";
import { Mermaid } from "./Mermaid";

const isHttp = (href?: string) => !!href && /^https?:\/\//i.test(href);

/** Markdown for answers and overviews. Links open in the system browser (http/https only);
 * with `mermaid`, ```mermaid blocks render as diagrams. */
export function Markdown({ children, mermaid = false }: { children: string; mermaid?: boolean }) {
  return (
    <ReactMarkdown
      components={{
        a: ({ href, children: label }) => (
          <a
            href={isHttp(href) ? href : undefined}
            className="text-accent underline underline-offset-2"
            onClick={(e) => {
              e.preventDefault();
              if (isHttp(href)) void window.kb?.openExternal(href!);
            }}
          >
            {label}
          </a>
        ),
        ...(mermaid
          ? {
              code({ className, children: code }) {
                const text = String(code).replace(/\n$/, "");
                if (className?.includes("language-mermaid")) return <Mermaid code={text} />;
                return <code className="font-mono text-xs">{code}</code>;
              },
              pre: ({ children: c }) => <>{c}</>,
            }
          : {}),
      }}
    >
      {children}
    </ReactMarkdown>
  );
}
