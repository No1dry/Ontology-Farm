import { useEffect, useRef, useState } from "react";
import {
  api,
  data,
  Diagnostic,
  execute,
  intent,
  type Models,
  useReady,
} from "../studio/api";
import { Documents } from "../studio/Documents";
import { sessionController } from "../../api/session";
import type { CommandIntent } from "../../shared/commands";
import "../studio/studio.css";
export default function ApprovalsPanel() {
  const ready = useReady();
  const [items, setItems] = useState<Models["ApprovalDetail"][]>([]);
  const [cursor, setCursor] = useState<string | null>();
  const [selected, setSelected] = useState(
    new URLSearchParams(location.search).get("approval") || "",
  );
  const [detail, setDetail] = useState<Models["ApprovalDetail"]>();
  const [draft, setDraft] = useState<Models["SchemaDraftView"]>();
  const [baseline, setBaseline] = useState<Models["SchemaReleaseView"]>();
  const [binding, setBinding] = useState(false);
  const [baselineState, setBaselineState] = useState<
    "current" | "stale" | "unavailable"
  >("unavailable");
  const [currentBaseline, setCurrentBaseline] = useState<string | null>();
  const [error, setError] = useState<unknown>();
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(false);
  const pending = useRef<CommandIntent | undefined>(undefined);
  async function list(more = false) {
    try {
      const page = data(
        await api.GET("/api/v1/approvals", {
          params: { query: { cursor: more ? cursor || undefined : undefined } },
        }),
      );
      setItems((old) => (more ? [...old, ...page.items] : page.items));
      setCursor(page.next_cursor);
    } catch (e) {
      setError(e);
    }
  }
  useEffect(() => {
    if (ready) void list();
  }, [ready]);
  useEffect(() => {
    if (!selected || !ready) return;
    let live = true;
    setDetail(undefined);
    setDraft(undefined);
    setBaseline(undefined);
    setBinding(false);
    setBaselineState("unavailable");
    setCurrentBaseline(undefined);
    setError(undefined);
    void (async () => {
      const item = data(
        await api.GET("/api/v1/approvals/{approval_id}", {
          params: { path: { approval_id: selected } },
        }),
      );
      if (!live) return;
      setDetail(item);
      if (
        item.command.operation !== "ontology.release" ||
        typeof item.command.draft_id !== "string"
      )
        return;
      const candidate = data(
        await api.GET("/api/v1/schema/drafts/{id}", {
          params: { path: { id: item.command.draft_id } },
        }),
      );
      const active = item.impact.active_release_id;
      const before =
        typeof active === "string"
          ? data(
              await api.GET("/api/v1/schema/releases/{id}", {
                params: { path: { id: active } },
              }),
            )
          : undefined;
      if (!live) return;
      setDraft(candidate);
      setBaseline(before);
      const current = candidate.current_baseline;
      const known =
        current &&
        (current.active_release_id === null ||
          typeof current.active_release_id === "string");
      const capturedKnown = active === null || typeof active === "string";
      setCurrentBaseline(known ? current.active_release_id : undefined);
      const baselineMatches =
        !!known &&
        capturedKnown &&
        active === current.active_release_id &&
        (!active || (before?.id === active && before.state === "published"));
      setBaselineState(
        !known || !capturedKnown
          ? "unavailable"
          : baselineMatches
            ? "current"
            : "stale",
      );
      setBinding(
        candidate.id === item.command.draft_id &&
          candidate.revision === item.command.revision &&
          typeof candidate.document_digest === "string" &&
          candidate.document_digest === item.command.document_digest &&
          baselineMatches,
      );
    })().catch((e) => {
      if (live) setError(e);
    });
    return () => {
      live = false;
    };
  }, [selected, ready]);
  useEffect(() => () => pending.current?.dispose(), []);
  async function decide(decision?: "approve" | "reject") {
    if (!detail) return;
    setBusy(true);
    setError(undefined);
    setRetry(false);
    try {
      if (decision) {
        pending.current?.dispose();
        pending.current = intent(
          `approvals/${detail.id}/decisions`,
          { decision, comment } satisfies Models["ApprovalDecision"],
          detail.revision,
        );
      }
      if (!pending.current) return;
      await execute(pending.current);
      pending.current.dispose();
      pending.current = undefined;
      setDetail(
        data(
          await api.GET("/api/v1/approvals/{approval_id}", {
            params: { path: { approval_id: detail.id } },
          }),
        ),
      );
      await list();
    } catch (e) {
      setError(e);
      if ((e as { status?: number }).status === 409) setBinding(false);
      setRetry(
        !(e as { status?: number }).status ||
          (e as { status: number }).status >= 500 ||
          (e as { status: number }).status === 401,
      );
      if (
        (e as { status?: number }).status &&
        (e as { status: number }).status < 500 &&
        (e as { status: number }).status !== 401
      ) {
        pending.current?.dispose();
        pending.current = undefined;
      }
    } finally {
      setBusy(false);
    }
  }
  const ownHighRisk =
    detail?.risk === "high" &&
    detail.submitter_id === sessionController.state.session?.user.id;
  return (
    <div className="studio-page">
      <div className="page-heading">
        <div>
          <p className="eyebrow">GOVERNANCE / INDEPENDENT REVIEW</p>
          <h1>治理与审查</h1>
          <p>逐项核对候选、实际基线、来源与当前授权。</p>
        </div>
      </div>
      <Diagnostic error={error} />
      <div className="review-layout">
        <aside className="panel">
          <h2>可见审核队列</h2>
          {items.map((a) => (
            <button
              className="approval-item"
              key={a.id}
              onClick={() => {
                setSelected(a.id);
                const url = new URL(location.href);
                url.searchParams.set("approval", a.id);
                history.replaceState(null, "", url);
              }}
            >
              <strong>
                {a.kind} · {a.status}
              </strong>
              <small>{a.id}</small>
              <small>
                {a.risk} · r{a.revision}
              </small>
            </button>
          ))}
          {cursor ? (
            <button disabled={!ready} onClick={() => void list(true)}>
              加载更多审核
            </button>
          ) : (
            <p>已显示全部当前可见审核。</p>
          )}
        </aside>
        <section className="panel">
          {detail ? (
            <>
              <h2>
                {detail.status === "approved"
                  ? "已批准"
                  : detail.status === "rejected"
                    ? "已拒绝"
                    : "待审核"}
              </h2>
              <p>
                风险 {detail.risk} · 提交者 {detail.submitter_id}
              </p>
              <pre>{JSON.stringify(detail, null, 2)}</pre>
              {draft && (
                <>
                  <p role={binding ? "status" : "alert"}>
                    {binding
                      ? "候选 revision 与 document_digest 精确匹配审批"
                      : "APPROVAL_STALE · 候选或基线未精确匹配此审批，禁止决定；请重新校验并提交审核"}
                  </p>
                  <p role={baselineState === "current" ? "status" : "alert"}>
                    {baselineState === "unavailable"
                      ? "当前活动基线不可用或捕获证据不完整 · 禁止决定；请重新读取或重新提交审核"
                      : baselineState === "stale"
                        ? "APPROVAL_BASELINE_STALE · 活动基线已变更，请重新校验并提交审核"
                        : "捕获基线与当前活动基线一致"}
                  </p>
                  <p>
                    当前活动基线：
                    {currentBaseline === undefined
                      ? "未知"
                      : currentBaseline || "无已发布基线"}
                    。此读取不替代服务端决定时的并发校验。
                  </p>
                  <Documents
                    baselineKind="captured"
                    candidate={draft.document}
                    baseline={baseline?.document}
                    baselineId={
                      typeof detail.impact.active_release_id === "string"
                        ? detail.impact.active_release_id
                        : null
                    }
                  />
                </>
              )}
              {detail.command.operation !== "ontology.release" && (
                <p>此审核类型尚未提供完整业务检查视图，请在对应功能中审核。</p>
              )}
              <label>
                审核意见
                <textarea
                  value={comment}
                  onChange={(e) => setComment(e.target.value)}
                  maxLength={4000}
                />
              </label>
              {ownHighRisk && <p>高风险提交者不能审核自己的变更。</p>}
              <button
                disabled={
                  !binding ||
                  !!ownHighRisk ||
                  busy ||
                  !ready ||
                  retry ||
                  !comment.trim() ||
                  detail.status !== "pending"
                }
                onClick={() => void decide("approve")}
              >
                审核通过
              </button>
              <button
                disabled={
                  !binding ||
                  busy ||
                  !ready ||
                  retry ||
                  !comment.trim() ||
                  detail.status !== "pending"
                }
                onClick={() => void decide("reject")}
              >
                拒绝
              </button>
              {retry && (
                <button disabled={!ready || busy} onClick={() => void decide()}>
                  重试原审核命令
                </button>
              )}
            </>
          ) : (
            <p>选择审核，读取完整候选与基线。</p>
          )}
        </section>
      </div>
    </div>
  );
}
