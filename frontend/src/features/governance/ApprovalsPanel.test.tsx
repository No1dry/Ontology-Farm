import { webcrypto } from "node:crypto";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ApprovalsPanel from "./ApprovalsPanel";
import { sessionController } from "../../api/session";
import fixtures from "../studio/fixtures/approval-documents.json";
import type { Document } from "../studio/DraftCommands";
const stableFixture = {
  ...fixtures[0],
  document: { ...fixtures[0].document, ui_metadata: {} },
  digest: "289e5a34fdc3a9044964540352d3959b4626767c72ef00849faf18d9d5242d4f",
};

afterEach(() => {
  cleanup();
  sessionController.receive("logout");
  history.replaceState(null, "", "/");
  vi.unstubAllGlobals();
});
async function openReview({
  fixture = stableFixture,
  captured = null as string | null,
  current = { active_release_id: null } as {
    active_release_id: string | null;
  } | null,
  revision = 1,
  digest = fixture.digest,
  omitCaptured = false,
  decisionStale = false,
}: {
  fixture?: { document: Document; digest: string };
  captured?: string | null;
  current?: { active_release_id: string | null } | null;
  revision?: number;
  digest?: string | null;
  omitCaptured?: boolean;
  decisionStale?: boolean;
} = {}) {
  vi.stubGlobal("crypto", webcrypto);
  const approval = {
    id: "approval",
    revision: 1,
    kind: "schema.publish",
    status: "pending",
    risk: "high",
    submitter_id: "editor",
    reasons: [],
    baseline: {},
    command: {
      operation: "ontology.release",
      draft_id: "draft",
      revision: 1,
      document_digest: fixture.digest,
      imports_digest: "imports",
    },
    impact: omitCaptured ? {} : { active_release_id: captured },
  };
  sessionController.transport = (async (request: Request) => {
    const url = new URL(request.url);
    if (url.pathname.endsWith("/session"))
      return Response.json({
        user: {
          id: "reviewer",
          issuer: "test",
          subject: "reviewer",
          display_name: "Reviewer",
          email: null,
          enabled: true,
          revision: 1,
        },
        permissions: [],
        authorization_version: 1,
        csrf_token: "csrf",
      });
    if (url.pathname.endsWith("/approvals"))
      return Response.json({ items: [approval], next_cursor: null });
    if (url.pathname.endsWith("/approvals/approval"))
      return Response.json(approval);
    if (url.pathname.endsWith("/decisions") && decisionStale)
      return Response.json(
        { code: "APPROVAL_STALE", message: "Baseline changed", details: {} },
        { status: 409 },
      );
    if (url.pathname.endsWith("/drafts/draft"))
      return Response.json({
        id: "draft",
        revision,
        base_release_id: null,
        document: fixture.document,
        document_digest: digest,
        current_baseline: current,
        state: "submitted",
      });
    if (url.pathname.includes("/releases/"))
      return Response.json({
        id: captured,
        revision: 1,
        version: "v1",
        state:
          captured === current?.active_release_id ? "published" : "deprecated",
        document: fixture.document,
        artifacts: [],
      });
    throw new Error(`Unexpected review request ${url.pathname}`);
  }) as typeof fetch;
  await sessionController.reconcile();
  history.replaceState(null, "", "/governance?approval=approval");
  render(<ApprovalsPanel />);
  await screen.findByText("完整文档对照");
  fireEvent.change(screen.getByLabelText("审核意见"), {
    target: { value: "Reviewed exact candidate" },
  });
  return screen.getByRole("button", { name: "审核通过" }) as HTMLButtonElement;
}
it.each(fixtures)(
  "allows exact server-bound $name metadata without JavaScript reserialization",
  async (fixture) => {
    const approve = await openReview({ fixture });
    await waitFor(() => expect(approve.disabled).toBe(false));
  },
);
it("allows an exact candidate when a visible captured release is still current", async () => {
  expect(
    (
      await openReview({
        captured: "active",
        current: { active_release_id: "active" },
      })
    ).disabled,
  ).toBe(false);
  expect(screen.getByText(/提交时捕获基线：active/)).toBeDefined();
  expect(screen.getByText(/当前活动基线：active/)).toBeDefined();
});
it("disables further decisions when server revalidation detects a later concurrent change", async () => {
  const approve = await openReview({ decisionStale: true });
  fireEvent.click(approve);
  await screen.findByText("APPROVAL_STALE", { exact: true });
  expect(approve.disabled).toBe(true);
  expect(
    (screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
});
it.each([
  { captured: "old", current: { active_release_id: "new" } },
  { captured: null, current: { active_release_id: "new" } },
])(
  "blocks an unchanged draft when its captured baseline $captured was superseded",
  async (value) => {
    const approve = await openReview(value);
    expect(approve.disabled).toBe(true);
    expect(
      (screen.getByRole("button", { name: "拒绝" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(screen.getByText(/APPROVAL_BASELINE_STALE/)).toBeDefined();
    expect(screen.queryByText(/实际活动基线：old/)).toBeNull();
  },
);
it("fails closed when current baseline evidence is unavailable", async () => {
  const approve = await openReview({ current: null });
  expect(approve.disabled).toBe(true);
  expect(screen.getByText(/当前活动基线不可用/)).toBeDefined();
});
it.each([
  { revision: 2 },
  { digest: "wrong" },
  { digest: null },
  { omitCaptured: true },
])(
  "fails closed for incomplete or mismatched exact evidence %j",
  async (value) => {
    expect((await openReview(value)).disabled).toBe(true);
  },
);
