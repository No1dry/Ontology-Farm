import { useState } from "react";
import {
  ReactFlow,
  Background,
  Controls,
  MarkerType,
  type Connection,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import type { Document } from "./DraftCommands";
export default function SchemaCanvas({
  document,
  select,
  inherit,
}: {
  document: Document;
  select: (iri: string) => void;
  inherit: (child: string, parent: string) => void;
}) {
  const [pending, setPending] = useState<Connection>();
  const classes = document.classes || [];
  const nodes = classes.map((c, i) => ({
    id: c.iri,
    position: { x: (i % 3) * 220, y: Math.floor(i / 3) * 140 },
    data: { label: c.label },
    style: { border: "1px solid #75907b", background: "#f8faf4" },
  }));
  const edges = classes
    .flatMap((c) =>
      (c.parents || []).map((p) => ({
        id: `${c.iri}->${p}`,
        source: c.iri,
        target: p,
        label: "继承",
        markerEnd: { type: MarkerType.ArrowClosed },
        style: { strokeDasharray: "5 4", stroke: "#5f7862" },
      })),
    )
    .concat(
      (document.relations || []).flatMap((r) =>
        (r.domain_iris || []).flatMap((d) =>
          (r.range_iris || []).map((t) => ({
            id: `${r.iri}:${d}:${t}`,
            source: d,
            target: t,
            label: r.label,
            markerEnd: { type: MarkerType.ArrowClosed },
            style: { strokeDasharray: "", stroke: "#2b8689" },
          })),
        ),
      ),
    );
  return (
    <section className="schema-view">
      <div className="panel-title">
        <h2>Schema 结构</h2>
        <small>虚线：继承 · 实线：业务关系</small>
      </div>
      <p className="studio-note">
        从子类拖向父类后确认。列表与检查器提供同等键盘操作；此处仅展示模型。
      </p>
      <div className="schema-canvas">
        <ReactFlow
          nodes={nodes}
          edges={edges}
          onNodeClick={(_, node) => select(node.id)}
          onConnect={setPending}
          fitView
        >
          <Background />
          <Controls />
        </ReactFlow>
      </div>
      {pending && (
        <div role="dialog" aria-label="确认继承方向">
          <p>
            子类 {pending.source} → 父类 {pending.target}
          </p>
          <button
            onClick={() => {
              inherit(pending.source!, pending.target!);
              setPending(undefined);
            }}
          >
            确认继承方向
          </button>
          <button onClick={() => setPending(undefined)}>取消</button>
        </div>
      )}
    </section>
  );
}
