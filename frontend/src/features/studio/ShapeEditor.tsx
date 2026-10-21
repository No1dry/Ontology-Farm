import { useMemo, useState } from "react";
import {
  addPropertyShape,
  constraints,
  shapeSubjects,
  setConstraint,
} from "./shapeRdf";
import { Diagnostic } from "./api";
export default function ShapeEditor({
  source,
  change,
}: {
  source: string;
  change: (source: string) => void;
}) {
  const [shape, setShape] = useState(""),
    [predicate, setPredicate] = useState("minCount"),
    [value, setValue] = useState("1");
  const [error, setError] = useState<unknown>();
  const [normalized, setNormalized] = useState(false);
  const [target, setTarget] = useState("");
  const [path, setPath] = useState("");
  const parsed = useMemo(() => {
    try {
      return { subjects: shapeSubjects(source), error: undefined };
    } catch (error) {
      return { subjects: [], error };
    }
  }, [source]);
  return (
    <section className="shape-editor">
      <h2>Shape 约束</h2>
      <p>
        必填与基数、值域、模式由 SHACL 保存。结构化编辑保留其他 RDF
        语句，可能规范化序列化；源码编辑保留所输入文本。
      </p>
      <div className="shape-controls">
        <label>
          约束目标类 IRI
          <input value={target} onChange={(e) => setTarget(e.target.value)} />
        </label>
        <label>
          约束属性 IRI
          <input value={path} onChange={(e) => setPath(e.target.value)} />
        </label>
        <button
          disabled={!target || !path || !!parsed.error}
          onClick={async () => {
            try {
              const next = await addPropertyShape(source, target, path);
              change(next);
              setShape(shapeSubjects(next).at(-1) || "");
              setError(undefined);
            } catch (e) {
              setError(e);
            }
          }}
        >
          添加属性 Shape
        </button>
        <label>
          Shape 主体
          <select value={shape} onChange={(e) => setShape(e.target.value)}>
            <option value="">选择主体</option>
            {parsed.subjects.map((s) => (
              <option key={s}>{s}</option>
            ))}
          </select>
        </label>
        <label>
          约束
          <select
            value={predicate}
            onChange={(e) => setPredicate(e.target.value)}
          >
            {constraints.map((c) => (
              <option key={c}>{c}</option>
            ))}
          </select>
        </label>
        <label>
          约束值
          <input
            value={value}
            onChange={(e) => setValue(e.target.value)}
            placeholder="留空删除此约束"
          />
        </label>
        <button
          disabled={!shape || !!parsed.error}
          onClick={async () => {
            try {
              const kind = [
                "class",
                "path",
                "datatype",
                "targetClass",
              ].includes(predicate)
                ? "iri"
                : predicate.endsWith("Count")
                  ? "integer"
                  : predicate === "pattern"
                    ? "string"
                    : "decimal";
              change(
                await setConstraint(source, shape, predicate, value, kind),
              );
              setNormalized(true);
              setError(undefined);
            } catch (e) {
              setError(e);
            }
          }}
        >
          应用约束
        </button>
      </div>
      {normalized && (
        <p role="status">Shape 已按 RDF 图规范化序列化；未声称字节相同。</p>
      )}
      <Diagnostic error={error || parsed.error} />
      <label>
        Shape 原始源码
        <textarea
          className="source-editor"
          spellCheck={false}
          value={source}
          onChange={(e) => change(e.target.value)}
        />
      </label>
      <p className="studio-note">
        高级构造可保留和查看；服务器保存与校验将明确拒绝不支持的语义构造，不会自动删除。
      </p>
    </section>
  );
}
