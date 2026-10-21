import { DataFactory, Parser, Store, Writer } from "n3";
export const SH = "http://www.w3.org/ns/shacl#";
const XSD = "http://www.w3.org/2001/XMLSchema#";
export async function addPropertyShape(
  source: string,
  classIri: string,
  pathIri: string,
) {
  parseShapes(source);
  for (const iri of [classIri, pathIri]) {
    if (!/^[a-zA-Z][\w+.-]*:[^\s<>"{}|^`\\]+$/.test(iri))
      throw new Error("SHAPE_IRI_REQUIRED");
  }
  // Fresh named subjects avoid collisions with source blank-node labels. Appending
  // absolute IRIs also leaves original prefixes, comments and bytes untouched.
  const id = crypto.randomUUID();
  return (
    source +
    `\n<urn:lzo:shape:${id}> a <${SH}NodeShape>; <${SH}targetClass> <${classIri}>; <${SH}property> <urn:lzo:shape:${id}:property> .\n<urn:lzo:shape:${id}:property> <${SH}path> <${pathIri}> .\n`
  );
}
export const constraints = [
  "minCount",
  "maxCount",
  "minInclusive",
  "maxInclusive",
  "minExclusive",
  "maxExclusive",
  "pattern",
  "datatype",
  "class",
  "path",
  "targetClass",
] as const;
export function parseShapes(source: string) {
  if (source.length > 1_000_000)
    throw new Error("SHAPE_EDITOR_LIMIT: 请拆分大于 1 MB 的 Shape 源码");
  let anonymous = "__lzo_anonymous_",
    sequence = 0;
  while (source.includes(anonymous)) anonymous += "_";
  return new Store(
    new Parser({
      format: "text/turtle",
      blankNodePrefix: "",
      factory: {
        ...DataFactory,
        blankNode: (value?: string) =>
          DataFactory.blankNode(value ?? anonymous + sequence++),
      },
    }).parse(source),
  );
}
export function shapeSubjects(source: string) {
  return [
    ...new Set(
      parseShapes(source)
        .getQuads(null, null, null, null)
        .filter((q) => q.predicate.value.startsWith(SH))
        .map((q) =>
          q.subject.termType === "BlankNode"
            ? "_:" + q.subject.value
            : q.subject.value,
        ),
    ),
  ];
}
export async function setConstraint(
  source: string,
  shape: string,
  predicate: string,
  value: string,
  kind: "iri" | "integer" | "decimal" | "string" = "string",
) {
  if (!(constraints as readonly string[]).includes(predicate))
    throw new Error("UNSUPPORTED_SHAPE_EDIT");
  const store = parseShapes(source);
  const subject = shape.startsWith("_:")
    ? DataFactory.blankNode(shape.slice(2))
    : DataFactory.namedNode(shape);
  if (!store.countQuads(subject, null, null, null))
    throw new Error("SHAPE_NOT_FOUND");
  const p = DataFactory.namedNode(SH + predicate);
  if (kind === "integer" && value && !/^\d+$/.test(value))
    throw new Error("SHAPE_INTEGER_REQUIRED");
  if (kind === "decimal" && value && !/^[+-]?(?:\d+\.?\d*|\.\d+)$/.test(value))
    throw new Error("SHAPE_NUMBER_REQUIRED");
  if (kind === "iri" && value && !/^[a-zA-Z][\w+.-]*:[^\s<>]+$/.test(value))
    throw new Error("SHAPE_IRI_REQUIRED");
  store.removeQuads(store.getQuads(subject, p, null, null));
  if (value)
    store.addQuad(
      subject,
      p,
      kind === "iri"
        ? DataFactory.namedNode(value)
        : DataFactory.literal(value, DataFactory.namedNode(XSD + kind)),
    );
  return new Promise<string>((resolve, reject) => {
    const writer = new Writer({ prefixes: { sh: SH, xsd: XSD } });
    writer.addQuads(store.getQuads(null, null, null, null));
    writer.end((error, text) => (error ? reject(error) : resolve(text || "")));
  });
}
