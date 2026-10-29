import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Markdown } from "./Markdown";

afterEach(() => { delete window.kb; });

test("link_opens_externally", async () => {
  const openExternal = vi.fn(async () => {});
  window.kb = { openExternal } as unknown as Window["kb"];
  render(<Markdown>{"See [docs](https://example.com/a)."}</Markdown>);
  await userEvent.click(screen.getByRole("link", { name: "docs" }));
  expect(openExternal).toHaveBeenCalledWith("https://example.com/a");
});

test("non_http_link_does_nothing", async () => {
  const openExternal = vi.fn(async () => {});
  window.kb = { openExternal } as unknown as Window["kb"];
  render(<Markdown>{"[x](javascript:alert(1))"}</Markdown>);
  await userEvent.click(screen.getByText("x"));
  expect(openExternal).not.toHaveBeenCalled();
});
