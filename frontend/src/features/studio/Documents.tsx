import { useState } from "react";
import { same, type Document } from "./DraftCommands";
import { api, data, Diagnostic, type Models } from "./api";
import { sessionController } from "../../api/session";
export function Documents({
  candidate,
  baseline,
  baselineId,
  baselineKind = "active",
}: {
  candidate: Document;
  baseline?: Document | null;
  baselineId?: string | null;
  baselineKind?: "active" | "captured";
}) {
  return (
    <section className="document-review">
      <h3>完整文档对照</h3>
      <p>
        {baselineKind === "captured" ? "提交时捕获基线" : "实际活动基线"}：
        {baselineId || "无已发布基线"}
        。以下为文档差异，不代表受影响实例数量。
      </p>
      <p>
        变更区段：
        {(
          [
            "classes",
            "properties",
            "relations",
            "shapes",
            "ui_metadata",
            "dependencies",
          ] as const
        )
          .filter((key) => !same(candidate[key], baseline?.[key]))
          .join("、") || "无文档字段变更"}
        。仅比较文档字段。
      </p>
      <div className="document-columns">
        <details open>
          <summary>候选完整文档</summary>
          <pre>{JSON.stringify(candidate, null, 2)}</pre>
        </details>
        <details open>
          <summary>
            {baselineKind === "captured"
              ? "提交时捕获基线完整文档"
              : "实际活动基线完整文档"}
          </summary>
          <pre>
            {baseline
              ? JSON.stringify(baseline, null, 2)
              : baselineId
                ? "基线尚未读回"
                : "首次发布"}
          </pre>
        </details>
      </div>
      <SourceArtifacts
        blobs={[
          ...(candidate.dependencies || []),
          ...(baseline?.dependencies || []),
        ]}
      />
    </section>
  );
}
export function SourceArtifacts({ blobs }: { blobs: Models["BlobRef"][] }) {
  const [opened, setOpened] = useState<
    Record<string, { metadata: Models["ArtifactDetail"]; text: string }>
  >({});
  const [error, setError] = useState<unknown>();
  const unique = [...new Map(blobs.map((b) => [b.version_id, b])).values()];
  return (
    <details>
      <summary>来源制品 · 当前可见原始内容 ({unique.length})</summary>
      <Diagnostic error={error} />
      {unique.map((b) => (
        <article key={b.version_id}>
          <p>
            <code>{b.version_id}</code> · {b.media_type} · {b.size_bytes} bytes
          </p>
          <p>SHA256 {b.sha256}</p>
          <button
            onClick={async () => {
              try {
                const metadata = data(
                  await api.GET("/api/v1/artifacts/{version_id}", {
                    params: { path: { version_id: b.version_id } },
                  }),
                );
                if (b.size_bytes > 1_000_000)
                  throw new Error("ARTIFACT_PREVIEW_LIMIT");
                const response = await sessionController.fetch(
                  new Request(
                    new URL(
                      `/api/v1/artifacts/${b.version_id}/content`,
                      location.origin,
                    ),
                  ),
                );
                if (!response.ok) throw new Error("ARTIFACT_UNAVAILABLE");
                const text = await response.text();
                setOpened((v) => ({
                  ...v,
                  [b.version_id]: { metadata, text },
                }));
                setError(undefined);
              } catch (e) {
                setError(e);
              }
            }}
          >
            读取来源制品
          </button>
          {opened[b.version_id] && (
            <>
              <pre>
                {JSON.stringify(opened[b.version_id].metadata, null, 2)}
              </pre>
              <pre>{opened[b.version_id].text}</pre>
            </>
          )}
        </article>
      ))}
      {!unique.length && <p>文档未声明来源制品。</p>}
    </details>
  );
}
