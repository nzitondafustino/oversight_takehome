import { QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { createQueryClient } from "@/api/queries";
import { RecordActions } from "@/components/management/RecordActions";

afterEach(() => vi.unstubAllGlobals());

function show(options: { deleteDisabled?: boolean } = {}) {
  const onDeleted = vi.fn();
  render(<QueryClientProvider client={createQueryClient()}><RecordActions path="/v1/evaluations/eval_1" title="evaluation" field="label" value="Original" onDeleted={onDeleted} {...options} /></QueryClientProvider>);
  return onDeleted;
}

it("requires confirmation and allows cancellation without sending a delete request", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  show();
  await userEvent.click(screen.getByRole("button", { name: "Delete evaluation" }));
  expect(screen.getByRole("dialog", { name: "Delete evaluation?" })).toHaveTextContent("Are you sure you want to delete this evaluation?");
  expect(fetch).not.toHaveBeenCalled();
  await userEvent.click(screen.getByRole("button", { name: "Cancel" }));
  expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  expect(fetch).not.toHaveBeenCalled();
});

it("sends confirmed DELETE and returns to the list only after success", async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ deleted: true }), { status: 200 })); vi.stubGlobal("fetch", fetch);
  const onDeleted = show();
  await userEvent.click(screen.getByRole("button", { name: "Delete evaluation" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm deletion" }));
  await waitFor(() => expect(onDeleted).toHaveBeenCalledOnce());
  expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: "DELETE" });
});

it("keeps a refused deletion visible with the server's dependency error", async () => {
  vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ error: { code: "evaluation_active", message: "Cancel or finish this evaluation first", details: null } }), { status: 409 })));
  const onDeleted = show();
  await userEvent.click(screen.getByRole("button", { name: "Delete evaluation" }));
  await userEvent.click(screen.getByRole("button", { name: "Confirm deletion" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("Cancel or finish this evaluation first");
  expect(onDeleted).not.toHaveBeenCalled();
  expect(screen.getByRole("dialog")).toBeInTheDocument();
});

it("updates a label through PATCH without creating a new evaluation", async () => {
  const fetch = vi.fn(async (_url: RequestInfo | URL, _init?: RequestInit) => new Response(JSON.stringify({ label: "Updated" }), { status: 200 })); vi.stubGlobal("fetch", fetch);
  show();
  await userEvent.click(screen.getByRole("button", { name: "Edit label" }));
  await userEvent.clear(screen.getByLabelText("New label"));
  await userEvent.type(screen.getByLabelText("New label"), "Updated");
  await userEvent.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  expect(fetch.mock.calls[0]?.[1]).toMatchObject({ method: "PATCH", body: '{"label":"Updated"}' });
});

it("disables deletion while an evaluation is active", () => {
  show({ deleteDisabled: true });
  expect(screen.getByRole("button", { name: "Delete evaluation" })).toBeDisabled();
});
