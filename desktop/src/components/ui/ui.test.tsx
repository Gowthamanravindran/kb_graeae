import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Badge } from "./badge";
import { Button } from "./button";
import { Dialog, DialogContent, DialogTitle, DialogTrigger } from "./dialog";

test("Button renders a real button with the primary style", () => {
  render(<Button>Analyse a branch</Button>);
  const b = screen.getByRole("button", { name: "Analyse a branch" });
  expect(b.tagName).toBe("BUTTON");
  expect(b.className).toContain("bg-accent");
});

test("Badge tones map to colours", () => {
  render(<><Badge tone="ok">Synced</Badge><Badge tone="warn">Stale</Badge><Badge tone="priv">Private</Badge></>);
  expect(screen.getByText("Synced").className).toContain("bg-ok-soft");
  expect(screen.getByText("Stale").className).toContain("bg-warn-soft");
  expect(screen.getByText("Private").className).toContain("bg-priv-soft");
});

test("Dialog opens, is labelled, and closes on Escape", async () => {
  render(
    <Dialog>
      <DialogTrigger asChild><Button>Open</Button></DialogTrigger>
      <DialogContent><DialogTitle>Add a project</DialogTitle><input aria-label="Path" /></DialogContent>
    </Dialog>,
  );
  await userEvent.click(screen.getByRole("button", { name: "Open" }));
  expect(screen.getByRole("dialog", { name: "Add a project" })).toBeInTheDocument();
  await userEvent.keyboard("{Escape}");
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
});
