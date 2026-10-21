import { useState } from "react";
import type { Document } from "./DraftCommands";
export default function ClassTree({
  document,
  selected,
  select,
  add,
}: {
  document: Document;
  selected: string;
  select: (iri: string) => void;
  add: () => void;
}) {
  const [search, setSearch] = useState("");
  const classes = document.classes || [];
  return (
    <section className="studio-tree">
      <div className="panel-title">
        <h2>类与来源</h2>
        <button onClick={add}>新建类</button>
      </div>
      <label>
        搜索类、父类或来源包
        <input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="名称 / IRI / 来源摘要"
        />
      </label>
      <ul className="class-list">
        {classes
          .filter((c) =>
            [
              c.label,
              c.iri,
              ...(c.parents || []),
              ...(document.dependencies || []).map((d) => d.sha256),
            ].some((v) => v.toLowerCase().includes(search.toLowerCase())),
          )
          .map((c) => (
            <li key={c.iri}>
              <button
                className={selected === c.iri ? "selected" : ""}
                onClick={() => select(c.iri)}
              >
                <span>
                  {c.deprecated ? "◌" : "◇"} {c.label}
                </span>
                <small>{c.iri}</small>
                <small>
                  父类：
                  {(c.parents || [])
                    .map((p) => classes.find((v) => v.iri === p)?.label || p)
                    .join("、") || "根类"}
                </small>
              </button>
            </li>
          ))}
      </ul>
      <p className="studio-note">
        来源依赖属于完整文档。类列表不将包依赖推断为逐类来源。
      </p>
      <details>
        <summary>来源包 ({document.dependencies?.length || 0})</summary>
        {document.dependencies?.map((b) => (
          <p key={b.version_id}>
            <code>{b.sha256}</code>
            <small>{b.media_type}</small>
          </p>
        ))}
      </details>
    </section>
  );
}
