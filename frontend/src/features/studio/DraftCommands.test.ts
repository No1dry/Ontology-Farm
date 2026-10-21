import { describe, expect, it } from "vitest";
import { DraftCommands, checkedDocument, type Draft } from "./DraftCommands";
const original: Draft = {
  id: "draft",
  revision: 3,
  state: "draft",
  base_release_id: null,
  document: {
    classes: [
      {
        iri: "urn:Sensor",
        label: "Sensor",
        parents: [],
        description: "",
        deprecated: false,
      },
    ],
    properties: [],
    relations: [],
    shapes: '# retained blank node\n<urn:shape> <urn:p> [ <urn:q> "a" ] .',
    ui_metadata: { field: { widget: "geometry-reference", custom: ["keep"] } },
    dependencies: [
      {
        sha256: "digest",
        size_bytes: 42,
        media_type: "text/turtle",
        version_id: "blob",
      },
    ],
  },
};
describe("immutable complete-document edit history", () => {
  it("acknowledges server JSON object key ordering without hiding a later edit", () => {
    const stack = new DraftCommands(original);
    stack.edit("metadata", (d) => ({ ...d, ui_metadata: { z: 1, a: 2 } }));
    stack.accept({
      ...original,
      revision: 4,
      document: { ...stack.document, ui_metadata: { a: 2, z: 1 } },
    });
    expect(stack.dirty).toBe(false);
    stack.edit("later", (d) => ({ ...d, ui_metadata: { a: 3, z: 1 } }));
    expect(stack.dirty).toBe(true);
  });
  it("rejects structurally invalid advanced JSON without changing local content", () => {
    const stack = new DraftCommands(original);
    for (const value of [
      null,
      {},
      { ...original.document, classes: [null] },
      { ...original.document, shapes: [] },
    ]) {
      expect(() => stack.edit("raw", () => checkedDocument(value))).toThrow(
        "DOCUMENT_STRUCTURE_INVALID",
      );
      expect(stack.document).toEqual(original.document);
    }
    expect(checkedDocument(original.document)).toEqual(original.document);
  });
  it("undo and redo preserve untouched source, metadata and dependency references", () => {
    const stack = new DraftCommands(structuredClone(original));
    stack.edit("rename", (d) => ({
      ...d,
      classes: d.classes?.map((c) => ({ ...c, label: "新传感器" })),
    }));
    expect(stack.document.classes?.[0].label).toBe("新传感器");
    expect(stack.document.shapes).toBe(original.document.shapes);
    expect(stack.document.dependencies).toEqual(original.document.dependencies);
    expect(stack.document.ui_metadata).toEqual(original.document.ui_metadata);
    expect(stack.saved.document.classes?.[0].label).toBe("Sensor");
    expect(stack.dirty).toBe(true);
    stack.undo();
    expect(stack.document).toEqual(original.document);
    stack.redo();
    expect(stack.document.classes?.[0].label).toBe("新传感器");
  });
  it("explicit rebase merges disjoint remote changes and rejects same-field conflict without losing local content", () => {
    const stack = new DraftCommands(structuredClone(original));
    stack.edit("rename", (d) => ({
      ...d,
      classes: [{ ...d.classes![0], label: "local" }],
    }));
    const remote = structuredClone(original);
    remote.revision = 4;
    remote.document.ui_metadata = { remote: true };
    stack.rebase(remote);
    expect(stack.saved.revision).toBe(4);
    expect(stack.document.ui_metadata).toEqual({ remote: true });
    expect(stack.document.classes?.[0].label).toBe("local");
    const conflict = structuredClone(remote);
    conflict.revision = 5;
    conflict.document.classes![0].label = "other";
    expect(() => stack.rebase(conflict)).toThrow("MERGE_CONFLICT");
    expect(stack.document.classes?.[0].label).toBe("local");
    expect(stack.saved.revision).toBe(4);
  });
  it("accepts only acknowledged server revision; edits after request remain unsaved", () => {
    const stack = new DraftCommands(structuredClone(original));
    stack.edit("first", (d) => ({ ...d, shapes: "first" }));
    const sent = structuredClone(stack.document);
    stack.edit("second", (d) => ({ ...d, shapes: "second" }));
    stack.accept({ ...original, revision: 4, document: sent });
    expect(stack.saved.document.shapes).toBe("first");
    expect(stack.document.shapes).toBe("second");
    expect(stack.dirty).toBe(true);
    stack.undo();
    expect(stack.document.shapes).toBe("first");
    expect(stack.dirty).toBe(false);
  });
});
