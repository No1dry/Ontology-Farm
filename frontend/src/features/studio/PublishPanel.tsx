import { useEffect, useRef, useState } from "react";
import type { Draft } from "./DraftCommands";
import {
  api,
  data,
  Diagnostic,
  execute,
  intent,
  type Models,
  useReady,
} from "./api";
import { Documents } from "./Documents";
import { publicationState } from "./publication";
import type { CommandIntent } from "../../shared/commands";
export default function PublishPanel({
  draft,
  dirty,
}: {
  draft: Draft;
  dirty: boolean;
}) {
  const isReady = useReady();
  const [impact, setImpact] = useState<Models["ImpactReport"]>();
  const [baseline, setBaseline] = useState<Models["SchemaReleaseView"]>();
  const [approval, setApproval] = useState<Models["ApprovalDetail"]>();
  const [job, setJob] = useState<Models["JobView"]>();
  const [release, setRelease] = useState<Models["SchemaReleaseView"]>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const pending = useRef<
    | { command: CommandIntent; accept: (v: unknown) => Promise<void> | void }
    | undefined
  >(undefined);
  const [retry, setRetry] = useState(false);
  async function perform(
    command?: CommandIntent,
    accept?: (v: unknown) => Promise<void> | void,
  ) {
    if (command && accept) {
      pending.current?.command.dispose();
      pending.current = { command, accept };
    }
    const current = pending.current;
    if (!current) return;
    setBusy(true);
    setError(undefined);
    setRetry(false);
    try {
      await current.accept(await execute(current.command));
      current.command.dispose();
      pending.current = undefined;
    } catch (e) {
      setError(e);
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
        current.command.dispose();
        pending.current = undefined;
      }
    } finally {
      setBusy(false);
    }
  }
  async function readApproval(id: string) {
    setApproval(
      data(
        await api.GET("/api/v1/approvals/{approval_id}", {
          params: { path: { approval_id: id } },
        }),
      ),
    );
  }
  useEffect(() => {
    setImpact(undefined);
    setBaseline(undefined);
  }, [draft.revision, dirty]);
  useEffect(() => {
    if (!isReady) return;
    const id = new URLSearchParams(location.search).get("approval");
    if (id) void readApproval(id).catch(setError);
  }, [isReady, draft.id]);
  useEffect(() => () => pending.current?.command.dispose(), []);
  const stale =
    approval &&
    (approval.command.draft_id !== draft.id ||
      approval.command.revision !== draft.revision ||
      dirty);
  async function poll() {
    if (!job || !isReady) return;
    try {
      const next = data(
        await api.GET("/api/v1/jobs/{job_id}", {
          params: { path: { job_id: job.job.id } },
        }),
      );
      setJob(next);
      const ref = next.result_ref;
      if (
        next.job.status === "succeeded" &&
        ref?.kind === "schema.release" &&
        ref.api_path === `/api/v1/schema/releases/${ref.resource_id}`
      )
        setRelease(
          data(
            await api.GET("/api/v1/schema/releases/{id}", {
              params: { path: { id: ref.resource_id } },
            }),
          ),
        );
    } catch (e) {
      setError(e);
    }
  }
  const state = job ? publicationState(job, release) : undefined;
  useEffect(() => {
    if (!job || !isReady || error || state !== "pending") return;
    const timer = window.setTimeout(() => void poll(), 1500);
    return () => clearTimeout(timer);
  }, [job, isReady, state, error]);
  const plan = draft.document.ui_metadata?.migration_plan as
    | Record<string, unknown>
    | undefined;
  const missingPlan =
    impact?.migration_required &&
    !["description", "validation", "rollback"].every(
      (k) => typeof plan?.[k] === "string" && String(plan[k]).trim(),
    );
  const violations =
    impact?.violations.filter((v) => v.severity === "Violation") || [];
  return (
    <section className="publish-panel">
      <div className="panel-title">
        <h2>校验、审核与发布</h2>
        <span>保存版本 r{draft.revision}</span>
      </div>
      <p>校验 → 实际影响与差异 → 提交 → 独立审核 → 发布读回</p>
      <Diagnostic error={error} />
      <button
        disabled={dirty || busy || !isReady || retry}
        onClick={() =>
          void perform(
            intent(
              `schema/drafts/${draft.id}/validate`,
              undefined,
              draft.revision,
            ),
            async (v) => {
              const next = v as Models["ImpactReport"];
              setImpact(next);
              setBaseline(undefined);
              if (next.active_release_id)
                setBaseline(
                  data(
                    await api.GET("/api/v1/schema/releases/{id}", {
                      params: { path: { id: next.active_release_id } },
                    }),
                  ),
                );
            },
          )
        }
      >
        校验并预览影响
      </button>
      {dirty && <p>请先保存本地修改；校验仅针对服务器版本。</p>}
      {impact && (
        <>
          <h3>
            {impact.risk === "high"
              ? "高风险 · 需要其他人员审核"
              : impact.risk === "low"
                ? "低风险 · 仍需校验与审计"
                : "风险未知"}
          </h3>
          <p>{impact.reasons.join("、")}</p>
          <p>
            可见受影响资源 {impact.affected_ids.length}；校验问题{" "}
            {impact.violations.length}。服务未提供实例数量明细。
          </p>
          <pre>
            {JSON.stringify(
              {
                digest: impact.digest,
                baseline: impact.baseline,
                affected_ids: impact.affected_ids,
                violations: impact.violations,
                form_diff: impact.form_diff,
                permission_diff: impact.permission_diff,
              },
              null,
              2,
            )}
          </pre>
          <Documents
            candidate={draft.document}
            baseline={baseline?.document}
            baselineId={impact.active_release_id}
          />
          {missingPlan && (
            <p role="alert">
              MIGRATION_PLAN_REQUIRED · 请保存完整迁移计划后重新校验。
            </p>
          )}
          {violations.length > 0 && (
            <p role="alert">
              MIGRATION_NOT_APPLIED · 当前数据仍有违规，禁止提交。
            </p>
          )}
          <button
            disabled={
              dirty ||
              busy ||
              !isReady ||
              retry ||
              !!missingPlan ||
              violations.length > 0 ||
              (!!impact.active_release_id && !baseline)
            }
            onClick={() =>
              void perform(
                intent(
                  `schema/drafts/${draft.id}/submit`,
                  {
                    impact_digest: impact.digest,
                  } satisfies Models["SubmitDraftRequest"],
                  draft.revision,
                ),
                async (v) => {
                  const ref = v as Models["ApprovalRef"];
                  const url = new URL(location.href);
                  url.searchParams.set("approval", ref.id);
                  history.replaceState(null, "", url);
                  await readApproval(ref.id);
                },
              )
            }
          >
            提交审核
          </button>
        </>
      )}
      {approval && (
        <div className="approval-status">
          <h3>
            审核{" "}
            {approval.status === "approved"
              ? "已批准"
              : approval.status === "rejected"
                ? "已拒绝"
                : "待审核"}
          </h3>
          <p>
            绑定草稿 r{String(approval.command.revision)} · 摘要{" "}
            {approval.change_digest}
          </p>
          {stale && (
            <p role="alert">APPROVAL_STALE · 草稿已变化，需重新校验与审核。</p>
          )}
          <a href={`/governance?approval=${approval.id}`}>查看审核</a>
          <button
            disabled={!isReady || busy}
            onClick={() => void readApproval(approval.id).catch(setError)}
          >
            刷新审核状态
          </button>
          <button
            disabled={
              stale ||
              approval.status !== "approved" ||
              busy ||
              !isReady ||
              retry ||
              !!job
            }
            onClick={() =>
              void perform(
                intent(
                  "schema/releases",
                  {
                    draft_id: draft.id,
                    approval_id: approval.id,
                  } satisfies Models["PublishReleaseRequest"],
                  draft.revision,
                ),
                (v) => {
                  const ref = v as Models["JobRef"];
                  setJob({
                    job: ref,
                    revision: 0,
                    progress: {},
                    result_ref: null,
                    error_code: null,
                  });
                },
              )
            }
          >
            发布已批准版本
          </button>
        </div>
      )}
      {retry && (
        <button disabled={busy || !isReady} onClick={() => void perform()}>
          重试原命令（相同请求与幂等键）
        </button>
      )}
      {job && (
        <div className="publication-state">
          <h3>
            {state === "published"
              ? "发布完成"
              : state === "superseded"
                ? "已发布，现已被替代"
                : state === "failed"
                  ? "发布失败"
                  : state === "paused"
                    ? "发布已暂停"
                    : state === "cancelled"
                      ? "发布已取消"
                      : state === "missing-result"
                        ? "作业完成但缺少可信发布结果"
                        : "发布处理中"}
          </h3>
          <p>
            作业 {job.job.id} · {job.job.status} {job.error_code}
          </p>
          <pre>{JSON.stringify(job.progress, null, 2)}</pre>
          {release && (
            <p>
              精确发布版本 <code>{release.id}</code> · {release.version} ·{" "}
              {release.state}
            </p>
          )}
          <p className="studio-note">Schema 投影完成不表示派生推理已更新。</p>
          <button
            disabled={!isReady}
            onClick={() => {
              setError(undefined);
              void poll();
            }}
          >
            重新读取作业
          </button>
          {["failed", "paused", "cancelled"].includes(state || "") && (
            <button
              onClick={() => {
                setJob(undefined);
                setRelease(undefined);
                setImpact(undefined);
                setApproval(undefined);
              }}
            >
              重新校验并发起新的审核发布
            </button>
          )}
          {["failed", "paused"].includes(state || "") && (
            <button
              disabled={!isReady || busy || retry}
              onClick={() =>
                void perform(
                  intent(
                    `jobs/${job.job.id}/${state === "paused" ? "resume" : "retry"}`,
                    undefined,
                    job.revision,
                  ),
                  (v) => {
                    setRelease(undefined);
                    setJob({
                      ...job,
                      job: v as Models["JobRef"],
                      result_ref: null,
                      error_code: null,
                    });
                  },
                )
              }
            >
              {state === "paused" ? "恢复原作业" : "重试失败作业"}
            </button>
          )}
        </div>
      )}
    </section>
  );
}
