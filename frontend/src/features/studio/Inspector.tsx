import { useContext, useState } from "react";
import { JsonBuffers } from "./JsonBuffers";
import type { Document } from "./DraftCommands";
import { api, data, Diagnostic, useRead } from "./api";
type Edit = (label: string, change: (d: Document) => Document) => void;
export default function Inspector({
  document,
  selected,
  draftId,
  revision,
  persisted,
  edit,
}: {
  document: Document;
  selected: string;
  draftId: string;
  revision: number;
  persisted: boolean;
  edit: Edit;
}) {
  const c = document.classes?.find((c) => c.iri === selected);
  const [property, setProperty] = useState("");
  const [relation, setRelation] = useState("");
  const form = useRead(
    async () =>
      selected && persisted
        ? data(
            await api.GET("/api/v1/schema/drafts/{id}/form-schema", {
              params: { path: { id: draftId }, query: { class_iri: selected } },
            }),
          )
        : undefined,
    `${draftId}:${revision}:${selected}:${persisted}`,
  );
  const patchClass = (
    patch: Partial<NonNullable<Document["classes"]>[number]>,
  ) =>
    edit("修改类", (d) => ({
      ...d,
      classes: d.classes?.map((v) =>
        v.iri === selected ? { ...v, ...patch } : v,
      ),
    }));
  const p = document.properties?.find((p) => p.iri === property);
  const r = document.relations?.find((r) => r.iri === relation);
  const uiFields = (document.ui_metadata?.fields || {}) as Record<
    string,
    Record<string, unknown>
  >;
  return (
    <section className="studio-inspector">
      <h2>检查器</h2>
      {c ? (
        <fieldset>
          <legend>类定义</legend>
          <label>
            类 IRI
            <input readOnly value={c.iri} />
          </label>
          <label>
            中文名称
            <input
              value={c.label}
              onChange={(e) => patchClass({ label: e.target.value })}
            />
          </label>
          <label>
            说明
            <textarea
              value={c.description || ""}
              onChange={(e) => patchClass({ description: e.target.value })}
            />
          </label>
          <label>
            父类
            <select
              multiple
              value={c.parents || []}
              onChange={(e) =>
                patchClass({
                  parents: [...e.target.selectedOptions].map((v) => v.value),
                })
              }
            >
              {document.classes?.map((v) => (
                <option key={v.iri} value={v.iri}>
                  {v.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            <input
              type="checkbox"
              checked={c.deprecated || false}
              onChange={(e) => patchClass({ deprecated: e.target.checked })}
            />{" "}
            声明弃用
          </label>
        </fieldset>
      ) : (
        <p>选择一个类查看定义。</p>
      )}
      <details open>
        <summary>继承属性 · 服务器已保存版本</summary>
        <Diagnostic error={form.error} />
        {form.value?.fields.map((f) => (
          <div className="inherited-field" key={f.iri}>
            <strong>
              {f.label} {f.required ? "· 必填" : ""}
            </strong>
            <small>{f.iri}</small>
            <small>
              来源：{form.value?.inherited_from[f.iri]?.join("、") || selected}
            </small>
            <small>
              基数 {f.min_count}…{f.max_count ?? "不限"} ·{" "}
              {f.datatype_iri || f.reference_classes.join("、")}
            </small>
          </div>
        ))}
      </details>
      <fieldset>
        <legend>数据属性 · 语义与所有权</legend>
        <label>
          选择数据属性
          <select
            value={property}
            onChange={(e) => setProperty(e.target.value)}
          >
            <option value="">请选择</option>
            {document.properties?.map((p) => (
              <option key={p.iri} value={p.iri}>
                {p.label}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={() => {
            const iri = "urn:lzo:property:" + crypto.randomUUID();
            edit("新建属性", (d) => ({
              ...d,
              properties: [
                ...(d.properties || []),
                {
                  iri,
                  label: "新属性",
                  datatype_iri: "http://www.w3.org/2001/XMLSchema#string",
                  owner_policy_iri: "urn:lzo:policy:platform-manual",
                  unit: null,
                  enum_values: [],
                  superproperties: [],
                },
              ],
            }));
            setProperty(iri);
          }}
        >
          新建属性
        </button>
        {p && (
          <>
            {(
              ["label", "datatype_iri", "owner_policy_iri", "unit"] as const
            ).map((key) => (
              <label key={key}>
                {
                  {
                    label: "属性名称",
                    datatype_iri: "数据类型 IRI",
                    owner_policy_iri: "声明所有权策略 IRI",
                    unit: "单位",
                  }[key]
                }
                <input
                  value={p[key] || ""}
                  onChange={(e) =>
                    edit("修改属性", (d) => ({
                      ...d,
                      properties: d.properties?.map((v) =>
                        v.iri === p.iri
                          ? {
                              ...v,
                              [key]:
                                e.target.value || (key === "unit" ? null : ""),
                            }
                          : v,
                      ),
                    }))
                  }
                />
              </label>
            ))}
            <JsonField
              target={`property:${p.iri}:enum`}
              label="枚举 RDF Terms"
              value={p.enum_values || []}
              commit={(v) =>
                edit("枚举", (d) => ({
                  ...d,
                  properties: d.properties?.map((q) =>
                    q.iri === p.iri
                      ? { ...q, enum_values: v as typeof p.enum_values }
                      : q,
                  ),
                }))
              }
            />
            <p className="studio-note">
              所有权声明不代表当前成员的有效编辑权限。
            </p>
            <label>
              UI 控件
              <select
                value={String(uiFields[p.iri]?.widget || "text")}
                onChange={(e) =>
                  edit("UI 控件", (d) => ({
                    ...d,
                    ui_metadata: {
                      ...d.ui_metadata,
                      fields: {
                        ...uiFields,
                        [p.iri]: { ...uiFields[p.iri], widget: e.target.value },
                      },
                    },
                  }))
                }
              >
                {[
                  "text",
                  "number",
                  "select",
                  "reference",
                  "geometry-reference",
                ].map((v) => (
                  <option key={v}>{v}</option>
                ))}
              </select>
            </label>
            <label>
              <input
                type="checkbox"
                checked={Boolean(uiFields[p.iri]?.read_only)}
                onChange={(e) =>
                  edit("UI 只读提示", (d) => ({
                    ...d,
                    ui_metadata: {
                      ...d.ui_metadata,
                      fields: {
                        ...uiFields,
                        [p.iri]: {
                          ...uiFields[p.iri],
                          read_only: e.target.checked,
                        },
                      },
                    },
                  }))
                }
              />{" "}
              UI 只读提示（不授予或撤销权限）
            </label>
          </>
        )}
      </fieldset>
      <fieldset>
        <legend>业务关系与端点</legend>
        <label>
          选择关系
          <select
            value={relation}
            onChange={(e) => setRelation(e.target.value)}
          >
            <option value="">请选择</option>
            {document.relations?.map((r) => (
              <option key={r.iri} value={r.iri}>
                {r.label}
              </option>
            ))}
          </select>
        </label>
        <button
          onClick={() => {
            const iri = "urn:lzo:relation:" + crypto.randomUUID();
            edit("新建关系", (d) => ({
              ...d,
              relations: [
                ...(d.relations || []),
                {
                  iri,
                  label: "新关系",
                  domain_iris: selected ? [selected] : [],
                  range_iris: [],
                  superproperties: [],
                  inverse_iri: null,
                  symmetric: false,
                  transitive: false,
                },
              ],
            }));
            setRelation(iri);
          }}
        >
          新建关系
        </button>
        {r && (
          <>
            <label>
              关系名称
              <input
                value={r.label}
                onChange={(e) =>
                  edit("关系名称", (d) => ({
                    ...d,
                    relations: d.relations?.map((v) =>
                      v.iri === r.iri ? { ...v, label: e.target.value } : v,
                    ),
                  }))
                }
              />
            </label>
            {(["domain_iris", "range_iris"] as const).map((key) => (
              <label key={key}>
                {key === "domain_iris" ? "起点类" : "终点类"}
                <select
                  multiple
                  value={r[key] || []}
                  onChange={(e) => {
                    const values = [...e.target.selectedOptions].map(
                      (o) => o.value,
                    );
                    edit("关系端点", (d) => ({
                      ...d,
                      relations: d.relations?.map((v) =>
                        v.iri === r.iri ? { ...v, [key]: values } : v,
                      ),
                    }));
                  }}
                >
                  {document.classes?.map((c) => (
                    <option key={c.iri} value={c.iri}>
                      {c.label}
                    </option>
                  ))}
                </select>
              </label>
            ))}
          </>
        )}
      </fieldset>
    </section>
  );
}
export function JsonField({
  target,
  label,
  value,
  commit,
}: {
  target: string;
  label: string;
  value: unknown;
  commit: (value: unknown) => void;
}) {
  const store = useContext(JsonBuffers);
  if (!store) throw new Error("JSON_BUFFER_CONTEXT_REQUIRED");
  const key = JSON.stringify([store.draftId, target]);
  const buffer = store.buffers[key];
  const original = JSON.stringify(value);
  const stale = !!buffer && buffer.original !== original;
  return (
    <label>
      {label}
      <textarea
        aria-label={label}
        value={buffer?.text ?? JSON.stringify(value, null, 2)}
        onChange={(e) =>
          store.update(key, {
            text: e.target.value,
            original: buffer?.original ?? original,
          })
        }
      />
      <button
        disabled={stale}
        onClick={() => {
          try {
            if (stale) return;
            commit(JSON.parse(buffer?.text ?? original));
            store.update(key);
          } catch (e) {
            store.update(key, {
              text: buffer?.text ?? original,
              original: buffer?.original ?? original,
              error: e,
            });
          }
        }}
      >
        应用{label}
      </button>
      {buffer && (
        <button onClick={() => store.update(key)}>丢弃{label}未应用文本</button>
      )}
      {stale && (
        <p role="alert">
          此文本的原始区段已有其他修改；未应用文本已保留。请复制核对后显式丢弃，再基于当前内容编辑。
        </p>
      )}
      <Diagnostic error={buffer?.error} />
    </label>
  );
}
