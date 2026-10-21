import { lazy, Suspense, useEffect, useRef, useState } from "react";
import {
  checkedDocument,
  DraftCommands,
  type Document,
  type Draft,
} from "./DraftCommands";
import {
  api,
  data,
  Diagnostic,
  execute,
  intent,
  type Models,
  useReady,
} from "./api";
import type { CommandIntent } from "../../shared/commands";
import ClassTree from "./ClassTree";
import Inspector, { JsonField } from "./Inspector";
import PublishPanel from "./PublishPanel";
import ImportPanel from "./ImportPanel";
import { JsonBuffers, useJsonBuffers } from "./JsonBuffers";
import "./studio.css";
const SchemaCanvas = lazy(() => import("./SchemaCanvas"));
const ShapeEditor = lazy(() => import("./ShapeEditor"));
const empty: Document = {
  classes: [],
  properties: [],
  relations: [],
  shapes: "",
  ui_metadata: {},
  dependencies: [],
};
export default function StudioPage(): React.JSX.Element {
  const ready = useReady();
  const [drafts, setDrafts] = useState<Draft[]>([]);
  const [cursor, setCursor] = useState<string | null>();
  const [releases, setReleases] = useState<Models["SchemaReleaseView"][]>([]);
  const [base, setBase] = useState("");
  const [releaseCursor, setReleaseCursor] = useState<string | null>();
  const [stack, setStack] = useState<DraftCommands>();
  const [, render] = useState(0);
  const [selected, setSelected] = useState("");
  const [error, setError] = useState<unknown>();
  const [busy, setBusy] = useState(false);
  const [importBusy, setImportBusy] = useState(false);
  const [retry, setRetry] = useState(false);
  const [remote, setRemote] = useState<Draft>();
  const [conflict, setConflict] = useState(false);
  const [showCanvas, setShowCanvas] = useState(false);
  const jsonBuffers = useJsonBuffers();
  const buffered = Object.keys(jsonBuffers.buffers).length > 0;
  const dirty = !!stack?.dirty || buffered;
  const selection = useRef(0);
  const stackRef = useRef(stack);
  stackRef.current = stack;
  const pending = useRef<
    { command: CommandIntent; accept: (value: Draft) => void } | undefined
  >(undefined);
  function selectDraft(draft: Draft) {
    selection.current++;
    setStack(new DraftCommands(draft));
    setSelected(draft.document.classes?.[0]?.iri || "");
    setError(undefined);
    setRemote(undefined);
    setConflict(false);
    const url = new URL(location.href);
    url.search = "";
    url.searchParams.set("draft", draft.id);
    history.replaceState(null, "", url);
  }
  function draftTransition() {
    const request = ++selection.current;
    const previous = stackRef.current;
    const document = previous?.document;
    const generation = jsonBuffers.generation.current;
    return (draft: Draft) => {
      if (request !== selection.current) return;
      if (
        stackRef.current !== previous ||
        previous?.document !== document ||
        previous?.dirty ||
        generation !== jsonBuffers.generation.current
      ) {
        setError(
          new Error(
            "DRAFT_SELECTION_CANCELLED · 等待期间产生了本地修改，已保留原草稿；处理后可从服务器列表重新选择",
          ),
        );
        return;
      }
      selectDraft(draft);
    };
  }
  async function requestDraft(id: string) {
    const accept = draftTransition();
    const request = selection.current;
    try {
      const draft = data(
        await api.GET("/api/v1/schema/drafts/{id}", {
          params: { path: { id } },
        }),
      );
      accept(draft);
    } catch (e) {
      if (request === selection.current) setError(e);
    }
  }
  async function list(more = false) {
    try {
      const result = data(
        await api.GET("/api/v1/schema/drafts", {
          params: { query: { cursor: more ? cursor || undefined : undefined } },
        }),
      );
      setDrafts((old) => (more ? [...old, ...result.items] : result.items));
      setCursor(result.next_cursor);
    } catch (e) {
      setError(e);
    }
  }
  useEffect(() => {
    if (!ready) return;
    void list();
    void api
      .GET("/api/v1/schema/releases")
      .then((r) => {
        const p = data(r);
        setReleases(p.items);
        setReleaseCursor(p.next_cursor);
      })
      .catch(setError);
    const id = new URLSearchParams(location.search).get("draft");
    if (id && !stack) void requestDraft(id);
  }, [ready]);
  useEffect(() => {
    const warn = (event: BeforeUnloadEvent) => {
      if (dirty || retry) {
        event.preventDefault();
        event.returnValue = "";
      }
    };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty, retry]);
  useEffect(() => () => pending.current?.command.dispose(), []);
  useEffect(
    () => () => {
      selection.current++;
    },
    [ready],
  );
  function edit(label: string, change: (d: Document) => Document) {
    stack?.edit(label, change);
    render((v) => v + 1);
  }
  async function perform(
    command?: CommandIntent,
    accept?: (value: Draft) => void,
  ) {
    if (command && accept) {
      pending.current?.command.dispose();
      pending.current = { command, accept };
    }
    if (!pending.current) return;
    const current = pending.current;
    setBusy(true);
    setError(undefined);
    setRetry(false);
    try {
      const value = await execute<Draft>(current.command);
      current.accept(value);
      current.command.dispose();
      pending.current = undefined;
      void list();
    } catch (e) {
      setError(e);
      const status = (e as { status?: number }).status;
      setRetry(!status || status >= 500 || status === 401);
      if (status && status < 500 && status !== 401) {
        current.command.dispose();
        pending.current = undefined;
      }
      if (status === 409) setConflict(true);
    } finally {
      setBusy(false);
      render((v) => v + 1);
    }
  }
  const doc = stack?.document;
  return (
    <JsonBuffers.Provider
      value={{ ...jsonBuffers, draftId: stack?.saved.id || "" }}
    >
      <div className="studio-page">
        <div className="page-heading">
          <div>
            <p className="eyebrow">ONTOLOGY STUDIO / SCHEMA</p>
            <h1>本体工作室</h1>
            <p>定义农业知识的共同语言，保留每一份来源。</p>
          </div>
          <span className="section-index">02 / 建模</span>
        </div>
        <div className="studio-toolbar">
          <label>
            新草稿基于
            <select value={base} onChange={(e) => setBase(e.target.value)}>
              <option value="">空文档</option>
              {releases
                .filter((r) => ["published", "deprecated"].includes(r.state))
                .map((r) => (
                  <option key={r.id} value={r.id}>
                    {r.document.classes[0]?.label || "版本"} · {r.version} ·{" "}
                    {r.state}
                  </option>
                ))}
            </select>
          </label>
          {releaseCursor && (
            <button
              disabled={!ready}
              onClick={() =>
                void api
                  .GET("/api/v1/schema/releases", {
                    params: { query: { cursor: releaseCursor } },
                  })
                  .then((r) => {
                    const p = data(r);
                    setReleases((old) => [...old, ...p.items]);
                    setReleaseCursor(p.next_cursor);
                  })
                  .catch(setError)
              }
            >
              更多基线版本
            </button>
          )}
          <label>
            服务器草稿
            <select
              value={stack?.saved.id || ""}
              disabled={dirty || busy || retry || importBusy || !ready}
              onChange={(e) => {
                const found = drafts.find((d) => d.id === e.target.value);
                if (found && !dirty) void requestDraft(found.id);
              }}
            >
              <option value="">选择草稿</option>
              {drafts.map((d) => (
                <option key={d.id} value={d.id}>
                  {d.document.classes?.[0]?.label || "空草稿"} · r{d.revision} ·{" "}
                  {d.id.slice(0, 8)}
                </option>
              ))}
            </select>
          </label>
          {cursor && (
            <button disabled={!ready} onClick={() => void list(true)}>
              更多草稿
            </button>
          )}
          <button
            disabled={busy || !ready || dirty || retry || importBusy}
            onClick={() => {
              const accept = draftTransition();
              void perform(
                intent("schema/drafts", {
                  document:
                    releases.find((r) => r.id === base)?.document || empty,
                  base_release_id: base || null,
                } satisfies Models["CreateDraftRequest"]),
                accept,
              );
            }}
          >
            新建草稿
          </button>
          {stack && (
            <>
              <button
                disabled={
                  !stack.dirty ||
                  buffered ||
                  busy ||
                  !ready ||
                  retry ||
                  conflict ||
                  importBusy
                }
                onClick={() =>
                  void perform(
                    intent(
                      `schema/drafts/${stack.saved.id}`,
                      {
                        document: stack.document,
                      } satisfies Models["UpdateDraftRequest"],
                      stack.saved.revision,
                      "PATCH",
                    ),
                    (d) => {
                      stack.accept(d);
                      setConflict(false);
                    },
                  )
                }
              >
                保存草稿
              </button>
              <button
                disabled={!stack.canUndo || busy}
                onClick={() => {
                  stack.undo();
                  render((v) => v + 1);
                }}
              >
                撤销
              </button>
              <button
                disabled={!stack.canRedo || busy}
                onClick={() => {
                  stack.redo();
                  render((v) => v + 1);
                }}
              >
                重做
              </button>
              <span role="status">
                {dirty || retry ? "本地修改尚未确认保存" : "已保存到服务器"}
              </span>
              <small>
                r{stack.saved.revision} · {stack.label}
              </small>
            </>
          )}
        </div>
        <Diagnostic error={error} />
        {buffered && (
          <details className="panel">
            <summary>
              保留的未应用 JSON 文本（{Object.keys(jsonBuffers.buffers).length}
              ）
            </summary>
            <p>
              这些文本尚未进入草稿操作栈。可返回原字段应用；字段删除或原区段变化后，请复制核对或显式丢弃。
            </p>
            {Object.entries(jsonBuffers.buffers).map(([key, buffer]) => (
              <article key={key}>
                <code>{JSON.parse(key).join(" / ")}</code>
                <pre>{buffer.text}</pre>
                <button
                  aria-label={`丢弃保留文本：${key}`}
                  onClick={() => jsonBuffers.update(key)}
                >
                  丢弃此保留文本
                </button>
              </article>
            ))}
          </details>
        )}
        {retry && (
          <button disabled={busy || !ready} onClick={() => void perform()}>
            重试原保存命令（相同请求与幂等键）
          </button>
        )}
        {conflict && stack && (
          <section className="conflict-panel">
            <h2>显式解决版本冲突</h2>
            <p>
              本地内容未丢失。读取当前版本后，可合并不冲突的区段；同区段冲突需手动编辑完整
              JSON。
            </p>
            <button
              disabled={!ready}
              onClick={async () => {
                try {
                  setRemote(
                    data(
                      await api.GET("/api/v1/schema/drafts/{id}", {
                        params: { path: { id: stack.saved.id } },
                      }),
                    ),
                  );
                } catch (e) {
                  setError(e);
                }
              }}
            >
              读取最新版本
            </button>
            {remote && (
              <>
                <details>
                  <summary>服务器 r{remote.revision} 完整内容</summary>
                  <pre>{JSON.stringify(remote.document, null, 2)}</pre>
                </details>
                <button
                  onClick={() => {
                    try {
                      stack.rebase(remote);
                      setConflict(false);
                      setRemote(undefined);
                      setError(undefined);
                      render((v) => v + 1);
                    } catch (e) {
                      setError(e);
                    }
                  }}
                >
                  显式合并无冲突区段
                </button>
                <JsonField
                  target="merge-document"
                  label="人工合并完整文档"
                  value={stack.document}
                  commit={(v) => {
                    stack.rebase(remote, checkedDocument(v));
                    setConflict(false);
                    setRemote(undefined);
                    setError(undefined);
                    render((n) => n + 1);
                  }}
                />
              </>
            )}
          </section>
        )}
        {stack && doc ? (
          <>
            <div className="studio-layout">
              <ClassTree
                document={doc}
                selected={selected}
                select={setSelected}
                add={() => {
                  const iri = "urn:lzo:class:" + crypto.randomUUID();
                  edit("新建类", (d) => ({
                    ...d,
                    classes: [
                      ...(d.classes || []),
                      {
                        iri,
                        label: "新类",
                        description: "",
                        parents: [],
                        deprecated: false,
                      },
                    ],
                  }));
                  setSelected(iri);
                }}
              />
              <div className="studio-center">
                <ImportPanel
                  key={stack.saved.id}
                  lock={setImportBusy}
                  draft={stack.saved}
                  dirty={dirty}
                  accepted={(d) => {
                    if (
                      stack.dirty ||
                      Object.keys(jsonBuffers.buffers).length
                    ) {
                      setRemote(d);
                      setConflict(true);
                      return;
                    }
                    setStack(new DraftCommands(d));
                    setSelected(d.document.classes[0]?.iri || "");
                  }}
                />
                <div className="panel-title">
                  <h2>模型定义</h2>
                  <button onClick={() => setShowCanvas(!showCanvas)}>
                    {showCanvas ? "收起画布" : "打开 Schema 画布"}
                  </button>
                </div>
                {showCanvas && (
                  <Suspense fallback={<p>加载结构画布…</p>}>
                    <SchemaCanvas
                      document={doc}
                      select={setSelected}
                      inherit={(child, parent) =>
                        edit("确认继承方向", (d) => ({
                          ...d,
                          classes: d.classes?.map((c) =>
                            c.iri === child
                              ? {
                                  ...c,
                                  parents: [
                                    ...new Set([...(c.parents || []), parent]),
                                  ],
                                }
                              : c,
                          ),
                        }))
                      }
                    />
                  </Suspense>
                )}
                <Suspense fallback={<p>加载 Shape 编辑器…</p>}>
                  <ShapeEditor
                    source={doc.shapes || ""}
                    change={(source) =>
                      edit("Shape 源码", (d) => ({ ...d, shapes: source }))
                    }
                  />
                </Suspense>
                <details>
                  <summary>完整文档源码 / 高级编辑</summary>
                  <JsonField
                    target="document"
                    label="完整 SchemaDocument"
                    value={doc}
                    commit={(value) =>
                      edit("完整文档编辑", () => checkedDocument(value))
                    }
                  />
                </details>
                <details>
                  <summary>迁移计划与 UI 元数据</summary>
                  <JsonField
                    target="ui-metadata"
                    label="UI 元数据"
                    value={doc.ui_metadata || {}}
                    commit={(value) =>
                      edit("UI 元数据", (d) => ({
                        ...d,
                        ui_metadata: value as Document["ui_metadata"],
                      }))
                    }
                  />
                  <p>
                    迁移计划保存在 migration_plan，包含
                    description、validation、rollback 三项非空文本。UI fields
                    控件提示独立于 OWL 语义及当前有效权限。
                  </p>
                </details>
              </div>
              <Inspector
                document={doc}
                selected={selected}
                draftId={stack.saved.id}
                revision={stack.saved.revision}
                persisted={stack.saved.document.classes.some(
                  (c) => c.iri === selected,
                )}
                edit={edit}
              />
            </div>
            <PublishPanel
              key={stack.saved.id}
              draft={stack.saved}
              dirty={dirty}
            />
          </>
        ) : (
          <section className="panel studio-empty">
            <h2>从一个类开始</h2>
            <p>
              新建草稿或打开服务器草稿。保存、校验、审核与发布均由真实服务确认。
            </p>
          </section>
        )}
      </div>
    </JsonBuffers.Provider>
  );
}
