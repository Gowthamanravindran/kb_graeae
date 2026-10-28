import { render, screen } from "@testing-library/react";
import { Library } from "../views/Library";
import { Sidebar } from "./Sidebar";
import { ErrorBoundary } from "./ErrorBoundary";
import { fakeApi, renderApp } from "../test-utils";

test("a render error shows a recovery screen, not a blank window", () => {
  const Boom = () => { throw new Error("kaboom"); };
  vi.spyOn(console, "error").mockImplementation(() => {});
  render(<ErrorBoundary><Boom /></ErrorBoundary>);
  expect(screen.getByRole("heading", { name: "Something went wrong" })).toBeInTheDocument();
  expect(screen.getByText(/kaboom/)).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Back to the Library" })).toBeInTheDocument();
});

test("a registry value that is not a URL does not crash the shell", async () => {
  const api = fakeApi({
    projects: vi.fn(async () => [{ name: "sop", config_path: "/x", registry: "kb.example.com", repos: [], linkers: [] }]),
    branches: vi.fn(async () => ({ rows: [], registry_error: null })),
  });
  renderApp(<><Sidebar /><Library /></>, api);
  expect((await screen.findAllByText(/kb\.example\.com/)).length).toBeGreaterThan(0);
});
