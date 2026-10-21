import { expect, it } from "vitest";
import { Parser, Store } from "n3";
import { setConstraint, addPropertyShape, shapeSubjects } from "./shapeRdf";
const SH = "http://www.w3.org/ns/shacl#";
const source = `@prefix sh: <${SH}>. @prefix x: <urn:>.
x:S a sh:NodeShape; sh:targetClass x:Sensor; sh:property [ sh:path x:p; sh:pattern "^safe"; sh:in ("a" "b") ].
x:P sh:path x:temperature; sh:minCount 0; sh:maxCount 3; sh:minInclusive 0; sh:message "保留"@zh .`;
it("edits the anonymous property selected from a separately parsed source", async () => {
  const selected = shapeSubjects(source).find((s) => s.startsWith("_:"))!;
  const next = new Store(
    new Parser().parse(
      await setConstraint(source, selected, "minCount", "1", "integer"),
    ),
  );
  const property = next.getQuads(null, SH + "path", "urn:p", null)[0].subject;
  expect(
    next.getQuads(property, SH + "minCount", null, null)[0].object.value,
  ).toBe("1");
  expect(next.getQuads(property, SH + "in", null, null)).toHaveLength(1);
});
it("attaches a new direct-path Shape without rewriting existing source", async () => {
  const result = await addPropertyShape(source, "urn:Subclass", "urn:newField");
  expect(result.startsWith(source)).toBe(true);
  const store = new Store(new Parser().parse(result));
  const root = store.getQuads(null, SH + "targetClass", "urn:Subclass", null)[0]
    ?.subject;
  expect(root).toBeDefined();
  const property = store.getQuads(root, SH + "property", null, null)[0]?.object;
  expect(property).toBeDefined();
  expect(
    store.getQuads(property, SH + "path", null, null)[0].object.value,
  ).toBe("urn:newField");
});
it("changes only the selected constraint while retaining connected blank nodes, lists and typed literals", async () => {
  const next = new Store(
    new Parser().parse(
      await setConstraint(source, "urn:P", "minCount", "1", "integer"),
    ),
  );
  expect(
    next.getQuads("urn:P", SH + "minCount", null, null)[0].object.value,
  ).toBe("1");
  expect(
    next.getQuads("urn:P", SH + "maxCount", null, null)[0].object.value,
  ).toBe("3");
  expect(next.getQuads(null, SH + "pattern", null, null)[0].object.value).toBe(
    "^safe",
  );
  expect(
    next
      .getQuads(
        null,
        "http://www.w3.org/1999/02/22-rdf-syntax-ns#first",
        null,
        null,
      )
      .map((q) => q.object.value)
      .sort(),
  ).toEqual(["a", "b"]);
  expect(
    next.getQuads("urn:P", SH + "message", null, null)[0].object,
  ).toMatchObject({ language: "zh", value: "保留" });
  expect(next.size).toBe(new Parser().parse(source).length);
});
it("refuses unsupported predicate or invalid syntax without silently replacing source", async () => {
  await expect(setConstraint(source, "urn:P", "or", "1")).rejects.toThrow(
    "UNSUPPORTED_SHAPE_EDIT",
  );
  await expect(
    setConstraint("invalid [", "urn:P", "minCount", "1", "integer"),
  ).rejects.toThrow();
});
