import { useEffect, useRef, useState } from "react";
import type { Draft } from "./DraftCommands";
import { CommandIntent } from "../../shared/commands";
import { sessionController } from "../../api/session";
import {
  api,
  data,
  Diagnostic,
  execute,
  intent,
  type Models,
  useReady,
} from "./api";
export default function ImportPanel({
  draft,
  dirty,
  accepted,
  lock,
}: {
  draft: Draft;
  dirty: boolean;
  accepted: (draft: Draft) => void;
  lock: (locked: boolean) => void;
}) {
  const ready = useReady();
  const [file, setFile] = useState<File>();
  const [kind, setKind] = useState<"schema" | "shapes">("schema");
  const [format, setFormat] = useState<"turtle" | "json-ld">("turtle");
  const [blob, setBlob] = useState<Models["BlobRef"]>();
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [retry, setRetry] = useState(false);
  useEffect(() => {
    lock(busy || retry);
  }, [busy, retry, lock]);
  const pending = useRef<
    | { command: CommandIntent; accept: (v: unknown) => void | Promise<void> }
    | undefined
  >(undefined);
  useEffect(() => () => pending.current?.command.dispose(), []);
  async function run(
    command?: CommandIntent,
    accept?: (v: unknown) => void | Promise<void>,
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
      const status = (e as { status?: number }).status;
      setRetry(!status || status >= 500 || status === 401);
      if (status && status < 500 && status !== 401) {
        current.command.dispose();
        pending.current = undefined;
      }
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>导入本地 Schema / Shape 来源</summary>
      <p>
        文件先保存为受保护的来源制品，再以服务器导入规则解析。未支持的内容保留为原始制品并显示诊断。
      </p>
      <Diagnostic error={error} />
      <label>
        本地语义文件
        <input
          type="file"
          accept=".ttl,.jsonld,.json"
          disabled={busy || retry}
          onChange={(e) => {
            setFile(e.target.files?.[0]);
            setBlob(undefined);
          }}
        />
      </label>
      <label>
        导入类型
        <select
          value={kind}
          disabled={busy || retry}
          onChange={(e) => setKind(e.target.value as typeof kind)}
        >
          <option value="schema">Schema</option>
          <option value="shapes">Shape</option>
        </select>
      </label>
      <label>
        文件格式
        <select
          value={format}
          disabled={busy || retry}
          onChange={(e) => setFormat(e.target.value as typeof format)}
        >
          <option value="turtle">Turtle</option>
          <option value="json-ld">JSON-LD</option>
        </select>
      </label>
      <button
        disabled={!file || busy || retry || dirty || !ready}
        onClick={() => {
          const url = new URL("/api/v1/artifacts", location.origin);
          url.searchParams.set("resource_id", draft.id);
          url.searchParams.set("filename", file!.name);
          const command = new CommandIntent(
            sessionController,
            new Request(url, {
              method: "POST",
              headers: {
                "Content-Type":
                  format === "turtle" ? "text/turtle" : "application/ld+json",
                "If-Match": `"${draft.revision}"`,
              },
              body: file,
            }),
          );
          void run(command, async (v) => {
            const a = v as Models["ArtifactDetail"];
            const refreshed = data(
              await api.GET("/api/v1/schema/drafts/{id}", {
                params: { path: { id: draft.id } },
              }),
            );
            accepted(refreshed);
            setBlob({
              version_id: a.version_id,
              sha256: a.sha256,
              size_bytes: a.size_bytes,
              media_type: a.media_type,
            });
          });
        }}
      >
        保存来源制品
      </button>
      {blob && (
        <>
          <p>
            来源制品 {blob.version_id} · {blob.sha256}
          </p>
          <button
            disabled={busy || retry || dirty || !ready}
            onClick={() =>
              void run(
                intent(
                  "schema/drafts/imports",
                  {
                    blob,
                    kind,
                    format,
                    target_draft_id: draft.id,
                    base_release_id: draft.base_release_id,
                  } satisfies Models["SemanticDraftImportBody"],
                  draft.revision,
                ),
                (v) => {
                  accepted(v as Draft);
                  setBlob(undefined);
                },
              )
            }
          >
            导入到当前草稿
          </button>
        </>
      )}
      {retry && (
        <button disabled={busy || !ready} onClick={() => void run()}>
          重试原导入命令
        </button>
      )}
    </details>
  );
}
