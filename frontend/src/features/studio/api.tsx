import { useEffect, useState, useSyncExternalStore } from "react";
import type { components } from "../../api/generated";
import { createApiClient } from "../../api/client";
import { sessionController } from "../../api/session";
import { CommandIntent } from "../../shared/commands";
export type Models = components["schemas"];
export const api = createApiClient();
export function ready() {
  return sessionController.state.status === "ready";
}
export function useReady() {
  return (
    useSyncExternalStore(
      sessionController.subscribe,
      sessionController.snapshot,
    ).status === "ready"
  );
}
export class StudioError extends Error {
  constructor(
    public code: string,
    public status = 0,
    public details?: unknown,
  ) {
    super(code);
  }
}
export function data<T>(result: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (!result.response.ok || result.data === undefined) {
    const e = result.error as { code?: string; details?: unknown } | undefined;
    throw new StudioError(
      e?.code || "REQUEST_FAILED",
      result.response.status,
      e?.details,
    );
  }
  return result.data;
}
export function intent(
  path: string,
  body?: unknown,
  revision?: number,
  method = "POST",
) {
  return new CommandIntent(
    sessionController,
    new Request(new URL("/api/v1/" + path, window.location.origin), {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(revision === undefined ? {} : { "If-Match": `"${revision}"` }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    }),
  );
}
export async function execute<T>(command: CommandIntent): Promise<T> {
  const response = await command.execute();
  const value = await response.json();
  if (!response.ok)
    throw new StudioError(
      value.code || "REQUEST_FAILED",
      response.status,
      value.details,
    );
  return value as T;
}
export function useRead<T>(load: () => Promise<T>, key: string) {
  const isReady = useReady();
  const [value, setValue] = useState<T>();
  const [error, setError] = useState<unknown>();
  useEffect(() => {
    if (!isReady) return;
    let live = true;
    setError(undefined);
    void load().then(
      (v) => {
        if (live) setValue(v);
      },
      (e) => {
        if (live) setError(e);
      },
    );
    return () => {
      live = false;
    };
  }, [key, isReady]); // load is deliberately keyed by its complete authorized read context.
  return { value, error };
}
export function Diagnostic({ error }: { error: unknown }) {
  if (!error) return null;
  const e = error as {
    code?: string;
    message?: string;
    status?: number;
    details?: unknown;
  };
  const code = e.code || e.message?.split(":")[0] || "CONNECTION_UNCONFIRMED";
  const explanations: Record<string, string> = {
    INHERITANCE_CYCLE: "继承包含循环。请调整父类；本地定义已保留。",
    INHERITANCE_CONFLICT:
      "继承的基数、类型或枚举约束冲突，请检查父类与 Shape。",
    SEMANTIC_PROFILE_UNSUPPORTED:
      "当前语义配置不支持此 Shape 构造。原始内容未删除，请检查机器诊断。",
    DEPENDENCY_MISSING: "定义引用的父类、属性或端点缺失，请补全依赖。",
    MERGE_CONFLICT: "同一区段存在并发修改，请在完整文档中人工合并后确认。",
    REVISION_CONFLICT: "版本冲突。未提交内容已保留；读取最新版本并显式合并。",
    APPROVAL_STALE: "审批或基线已失效，请重新校验并提交审核。",
    MIGRATION_PLAN_REQUIRED: "需要完整迁移计划：说明、验证及回滚。",
    MIGRATION_NOT_APPLIED: "当前数据仍违反约束，请先完成迁移。",
    UNSUPPORTED_SHAPE_EDIT:
      "此构造不支持结构化编辑。原始源码保留，请在源码中修改。",
    FORBIDDEN: "当前权限不足，操作未完成。",
    NOT_FOUND: "资源不存在或当前不可见。",
  };
  return (
    <div role="alert" className="studio-error">
      <strong>{code}</strong>
      <p>
        {explanations[code] ||
          (e.status === 409
            ? "版本冲突。保留了本地编辑，请显式合并。"
            : "操作未完成。检查约束、当前权限或连接；未确认保存的内容仍留在本页。")}
      </p>
      {e.details !== undefined && (
        <pre>{JSON.stringify(e.details, null, 2)}</pre>
      )}
    </div>
  );
}
