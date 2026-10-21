import type { components } from "../../api/generated";
export type Document = components["schemas"]["SchemaDocument"];
export type Draft = components["schemas"]["SchemaDraftView"];
export function checkedDocument(value: unknown): Document {
  const object = (v: unknown): v is Record<string, unknown> =>
    !!v && typeof v === "object" && !Array.isArray(v);
  const invalid = () => {
    throw new Error(
      "DOCUMENT_STRUCTURE_INVALID: 完整文档字段结构无效，源码尚未应用",
    );
  };
  if (!object(value)) return invalid();
  for (const key of ["classes", "properties", "relations", "dependencies"]) {
    if (!Array.isArray(value[key]) || !value[key].every(object))
      return invalid();
  }
  for (const key of ["classes", "properties", "relations"]) {
    for (const item of value[key] as Record<string, unknown>[]) {
      if (typeof item.iri !== "string" || typeof item.label !== "string")
        return invalid();
      for (const array of [
        "parents",
        "domain_iris",
        "range_iris",
        "superproperties",
      ]) {
        if (
          item[array] !== undefined &&
          (!Array.isArray(item[array]) ||
            !item[array].every((v) => typeof v === "string"))
        )
          return invalid();
      }
    }
  }
  if (typeof value.shapes !== "string" || !object(value.ui_metadata))
    return invalid();
  return value as Document;
}
export function same(a: unknown, b: unknown): boolean {
  if (Object.is(a, b)) return true;
  if (!a || !b || typeof a !== "object" || typeof b !== "object") return false;
  if (Array.isArray(a) || Array.isArray(b))
    return (
      Array.isArray(a) &&
      Array.isArray(b) &&
      a.length === b.length &&
      a.every((v, i) => same(v, b[i]))
    );
  const left = a as Record<string, unknown>,
    right = b as Record<string, unknown>;
  const keys = Object.keys(left);
  return (
    keys.length === Object.keys(right).length &&
    keys.every((k) => Object.hasOwn(right, k) && same(left[k], right[k]))
  );
}
function freeze<T>(value: T): T {
  if (value && typeof value === "object") {
    Object.values(value).forEach(freeze);
    Object.freeze(value);
  }
  return value;
}
// Deliberately conservative: arrays and RDF source are atomic merge units.
// Concurrent changes to them require an explicit human source merge.
function merge(
  base: unknown,
  local: unknown,
  remote: unknown,
  path = "document",
): unknown {
  if (same(local, base)) return remote;
  if (same(remote, base) || same(local, remote)) return local;
  if (
    [base, local, remote].every(
      (v) => v !== null && typeof v === "object" && !Array.isArray(v),
    )
  ) {
    const b = base as Record<string, unknown>,
      l = local as Record<string, unknown>,
      r = remote as Record<string, unknown>;
    return Object.fromEntries(
      [...new Set([...Object.keys(b), ...Object.keys(l), ...Object.keys(r)])]
        .map((k) => [k, merge(b[k], l[k], r[k], `${path}.${k}`)])
        .filter(([, v]) => v !== undefined),
    );
  }
  throw new Error(`MERGE_CONFLICT: ${path}`);
}
export class DraftCommands {
  private snapshots: { label: string; document: Document }[];
  private index = 0;
  constructor(public saved: Draft) {
    this.saved = freeze(structuredClone(saved));
    this.snapshots = [{ label: "服务器版本", document: this.saved.document }];
  }
  get document(): Document {
    return this.snapshots[this.index].document;
  }
  get dirty() {
    return !same(this.document, this.saved.document);
  }
  get canUndo() {
    return this.index > 0;
  }
  get canRedo() {
    return this.index < this.snapshots.length - 1;
  }
  get label() {
    return this.snapshots[this.index].label;
  }
  edit(label: string, change: (document: Document) => Document) {
    const next = freeze(structuredClone(change(this.document)));
    if (same(next, this.document)) return;
    this.snapshots = this.snapshots.slice(0, this.index + 1);
    this.snapshots.push({ label, document: next });
    this.index++;
  }
  undo() {
    if (this.canUndo) this.index--;
  }
  redo() {
    if (this.canRedo) this.index++;
  }
  rebase(remote: Draft, resolved?: Document) {
    const next =
      resolved ??
      (merge(this.saved.document, this.document, remote.document) as Document);
    this.saved = freeze(structuredClone(remote));
    this.snapshots = [{ label: "服务器版本", document: this.saved.document }];
    this.index = 0;
    this.edit("显式合并", () => next);
  }
  accept(draft: Draft) {
    this.saved = freeze(structuredClone(draft));
  }
}
