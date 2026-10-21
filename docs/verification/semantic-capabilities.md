# Semantic adapter capability contract

S01 uses genuine Apache Jena Fuseki 6.2.0/TDB2 over private HTTP, RDFLib
7.6.0, Semantica 0.7.0 with its `shacl` extra, pySHACL 0.30.1, and
PyLD 3.3.0. There is no in-memory persistence fallback. Native Windows
acceptance is supplemental: the mandatory Compose, container restart, Linux
dependency closure, and offline image delivery gates remain pending.

## Logical RDF and the physical storage boundary

`RdfTerm` and `Triple` represent exact RDF terms. Language and datatype are
exclusive. IRIs must be absolute and cannot contain SPARQL delimiter/control
characters. Invalid supported lexical forms fail before adoption. Plain
strings and explicit `xsd:string` normalize to one explicit string datatype;
language tags normalize to lowercase. Duplicate identical statements are one
set member. Decimal `12.50` and `12.5` remain two distinct members.

TDB2 stores typed numeric values canonically. A native `SELECT`, GSP response,
or count therefore is **not** the logical graph. Two decimal spellings on the
same subject/predicate collapse to one physical statement. Public queries,
SHACL, inference, and export must reconstruct before evaluating filters,
`STR`, `sameTerm`, counts, or constraints. They must never repair strings
after a native query has already discarded rows.

Each managed graph has immutable physical and companion graph IRIs. Its
companion contains a version-1 manifest (logical graph, physical graph, digest,
count) and standard `rdf:Statement` records for every logical member. Each
record includes `rdf:subject`, `rdf:predicate`, typed `rdf:object`, member
manifest, kind, exact lexical **xsd:string**, and datatype or language.
The additional lexical metadata is application vocabulary, not a claim that
ordinary RDF reification preserves source spellings. Graph-member IDs are
separate from future governance assertion/support IDs.

`replace_graph` atomically stages a complete pair, verifies it, then atomically
switches its pointer. Unpublished preparation is safe to replay.
`read_graph` resolves and pins that immutable pair, then reads it remotely and
checks manifest identity, every record, digest, count, and correspondence with
the genuine typed graph. Physical comparison uses same-datatype value equality
where TDB2 has canonicalized values; lexical companion strings still require
exact equality. Missing, extra, corrupt, mismatched or unmanaged nonempty
data raises `RepresentationError`. Existing damaged pairs are never repaired
as an incidental replay. Empty revisions have manifests even though GSP may
return 404 for their empty physical graph. Truly absent graphs return `()`.

`urn:lzo:internal:` is reserved for manifests, physical graphs and records;
imports cannot impersonate it. `urn:lzo:skolem:` is reserved for generated
document mappings. Metadata possession grants no authority. S05 must build
its authorized Dataset solely from reconstructed logical graphs and authorized
provenance, excluding internal graphs from `GRAPH`, paths, counts, proofs and
exports. S01 exposes no public query/update endpoint. Fuseki ports remain on
the private backend network (native development binds loopback).

RDFLib normalization is disabled once when `lzo.semantic` initializes and in
its isolated semantic child processes; no request toggles global flags.
`canonical_query` normalizes literal nodes in parsed SPARQL algebra so RDF 1.1
plain/explicit strings and language case agree at the query constant boundary.
`logical_graph(...).query` uses it. S05 must additionally apply authorization
and its query safety/complexity profile; this helper is not a public proxy.

## Files, lexical profile, and blank nodes

`parse_semantic_document(BlobRef, "turtle" | "json-ld")` reads original bytes
through F04 `ArtifactStore`, verifies their SHA-256, and preserves default and
named graph boundaries. `semantic_file_context(store)` supplies an explicit
store; otherwise runtime settings select it. Parsing never rewrites the source
artifact. JSON-LD remote/scoped remote contexts and `@import` are rejected
before parsing. OWL import triples are rejected; Turtle itself does not load
imports. No unbundled dependency is resolved automatically.

| Profile | Behavior and exercised examples |
| --- | --- |
| IRIs, Unicode strings, escaping, language | Exact logical terms, Chinese language literals, quotes, slash and newline round trips |
| Decimal/integer/boolean | Exact `12.50`, `12.5`, `001`, `1`; numeric equality distinct from term equality; invalid decimal exponents/NaN rejected |
| Date/dateTime/double | Exact `2026-10-20`, `2026-10-20T01:02:03+08:00`, `1.25E2` through remote persistence and export |
| String equivalence | Implicit/explicit xsd:string are one logical term, including parsed query constants |
| base64Binary | XML whitespace is accepted without rewriting the stored spelling; alphabet, complete units, padding position/count and zero unused pad bits are checked explicitly |
| Named graphs | JSON-LD keeps separate graph contexts and shared within-document blank nodes |
| `xsd:token`, `xsd:normalizedString` | Explicit unsupported-profile errors: RDFLib rewrites whitespace even with normalization off |
| Unsupported XSD/structured RDF literal forms | Explicit profile error; no unrestricted datatype fidelity claim |
| Custom datatype IRIs | Retained as opaque typed lexical strings, without claiming custom datatype validity |
| RDF-star/generalized RDF, complex SHACL result paths | Not accepted by this first-release adapter profile |

RDFLib validates supported datatype values in addition to the adapter's numeric
and temporal lexical grammar. The exercised matrix is evidence for those forms,
not proof of every XML Schema datatype or edge lexical spelling.
For base64Binary, the adapter checks a temporary view with only XML whitespace
removed, then preserves the original term. Empty and whitespace-only spellings
are valid; non-XML whitespace, malformed padding and nonzero unused bits fail.
This follows the [XML Schema base64Binary lexical grammar](https://www.w3.org/TR/xmlschema-2/#base64Binary);
RDFLib's permissive decoder alone is insufficient.

Canonicalization calls genuine public `pyld.jsonld.normalize` on a whole
document's N-Quads, using **URDNA2015/PyLD-3.3.0** and a rejecting document loader.
It is not advertised as RDFC-1.0. RDFLib's genuine N-Triples serializer supplies
escaped literal syntax; `Literal.n3()` is not used as an N-Quads serializer.
PyLD's public structured N-Quads parser preserves canonical labels because
RDFLib reparsing otherwise randomizes those labels.

`skolemize(document)` returns the dataset, explicit mapping, canonical digest,
algorithm and source BlobRef. IRIs bind the stable artifact `version_id`
(independent import/document scope), canonical dataset digest and canonical
node ID. Byte-identical independent uploads have separate scopes. Whole-dataset
canonicalization preserves shared nodes across graph names and statements.
Symmetric nodes have no intrinsic individual identity; the guarantee is
dataset isomorphism. Producers must persist the returned map/digest/algorithm
with their immutable document revision. `reverse_skolem` reverses only that
recorded map, never a guessed prefix.

Lossless JSON-LD export calls genuine public
`rdflib.plugins.serializers.jsonld.from_rdf(dataset, use_native_types=False)`,
then `json.dumps`. RDFLib 7.6's `Dataset.serialize(..., use_native_types=False)`
wraps the option in a truthy tuple and loses integer/boolean spelling; it is
not used. The public function currently emits upstream deprecation warnings
for Dataset APIs; these are documented, not suppressed or patched.

## Genuine Semantica validation

`SemanticaAdapter.validate(data_ttl, shapes_ttl, bundle)` always calls the normal
public `from semantica.ontology import run_shacl_validation` import. No copied
SDK function, import bypass, injected module, model weights, or SDK JenaStore
fallback is involved. That public function calls pySHACL with `inference="none"`
and `abort_on_first=False`. `OntologyValidator.validate` and OWL serialization
are not accepted as inference or conformance evidence.

Semantica's `raw_report` is human-readable **results_text**, not RDF.
Its structured values lose datatype/language. Consequently a second genuine
public `pyshacl.validate` call on the identical prepared input obtains the
standard RDF result graph. Both passes must succeed; conforms and sorted result
identities (focus/path/shape/constraint/severity/value string) must agree.
Mismatch raises an error. The companion is never used as a fallback.
Duplicate validation cost is intentional and must be measured in D04.

Both passes evaluate original RDF node kinds. Canonical blank nodes in the
data and shape graphs receive separate scoped **blank-node labels**, including
RDF-list members. The Semantica pass receives explicit expanded JSON-LD with
`_:label` IDs and string-valued literal records: its genuine JSON-LD parser
retains those labels without list compaction or conversion to IRIs. The
companion receives the equivalent prepared Graph. Stable identities are
compared before report mapping. A per-call recorded map converts blank focus,
value and source-shape nodes to deterministic report-location IRIs only after
evaluation; those IRIs identify report locations, not the node kinds evaluated
by SHACL. Existing input skolem IRIs remain IRIs. Ordinary blank-node focus,
value, shape-target and RDF-list validation is supported.

`ValidationReport.violations` contains all reported severities, distinguished
by `ValidationIssue.severity`, including Warning and Info. A report with zero
Violation severity may still have `conforms=False`. Values retain RDF type
and language; constraints have full SHACL IRIs. Anonymous source shapes have
deterministic bundle-scoped IRIs. An optional nonempty `bundle.shapes_digest`
is SHA-256 of the exact submitted shape Turtle bytes and is checked before
execution. S02 must provide that binding for its published bundles.

The whitelist in `semantica_adapter.CORE` supports SHACL Core targets, direct
IRI paths, counts, class/datatype/node kind, ranges, length/pattern/language,
value comparisons, hasValue/in, boolean/node constraints, qualified counts,
closed shapes, severity/message and metadata. SPARQL constraints/targets,
SERVICE, JavaScript, rules, custom executable constraint components, remote
imports, and complex paths are explicitly rejected, never silently ignored.
This first release supplies common constraints, not arbitrary uploaded code.

Each parse/canonicalization/validation runs in an owned child process with a
fixed socket/DNS/URL audit denial. No per-request global network monkeypatch
is used. Parent process monitoring kills the child on deadline or RSS budget;
errors, dependency absence, over-budget work and malformed reports fail closed.
`LZO_SEMANTIC_MAX_BYTES` (2 MiB), `MAX_QUADS` (20,000), `MAX_BNODES` (256),
`TIMEOUT_SECONDS` (20), and `MEMORY_MB` (768), with the same `LZO_SEMANTIC_`
prefix, are deployment budgets, not capacity promises. Validation's constructor
can override its deadline. Child stdout and source sizes are also bounded.
The trusted validator accepts existing platform skolem IRIs from reconstructed
graphs; artifact ingestion still rejects caller imitation of that namespace.

## Trusted producer contract for S02/S03/I05/S04

Register deployment code with:

```python
register_projection(event_type, resolver, version="producer-v1",
                    dirty_kind="facts", action="edit")
```

The resolver receives `(uow, immutable_revision_row)` and returns
`ProjectionContent(graphs, references, dependency_revisions)`. Graph keys are
`schema`, `shape`, `candidate`, `accepted`, `provenance`, `derived`; omitted roles
are empty in a **complete replacement generation**, never implicitly inherited.
Every secondary protected reference must have an exact PG `RevisionRef`.
References start with the primary resource. Resolve content from the supplied
immutable payload and pinned versions/artifacts, never latest mutable state
relabeled with an old revision. This is trusted server code, not uploaded
triples, modules, risk flags or caller-controlled action names.

The command must validate current authorization before its first ledger write,
perform any required F06 gate, append its authoritative revision, call
`ProjectionService(uow, repository).schedule(actor, revision)` and commit once.
Schedule stores pending state, graph digest, producer version, exact dependency
snapshots and all-reference F04 history dependencies, calls
`mark_inference_dirty(uow, revision, kind)`, and enqueues the actual F05 outbox
identity. It never commits. Domain producers must make registration part of the
originating transaction. Callers cannot treat queued/pending as synchronized.

Both API and worker load `lzo.worker.handlers` plus trusted domain resolver
modules via `LZO_WORKER_HANDLER_MODULES`. `rdf.project` resolves its required
operation action from the authoritative event registration, freezes it in the
F05 job and rechecks it at claim/dispatch/batch/control boundaries. A schema
producer can require `schema.publish` without granting generic `edit`.
View-only users cannot enqueue, pause, resume, retry or execute that operation.
Worker identity remains the original initiator, with fresh current rights on
every protected reference; there is no administrative fallback.

`ProjectionService.project_revision` reads only authoritative PG revisions.
The exact public facade `project_revision(revision)` is bound with
`projection_context(uow, repository, actor)` and leaves commit to its caller.
Direct service calls without actor are trusted internal tooling, not routes.
Worker batches pass the fresh actor and own commit/checkpoint.

All six immutable graph pairs, the generation manifest and a durable prepared
marker are created in one conditional Jena Update transaction. A competing
same-identity staging writer cannot merge a different input position into an
existing manifest. Existing and newly created manifests must equal the complete
expected triple set before publication. Missing fields, extra fields or a
deleted marked manifest fail without repair or changing live role pointers.
Existing complete generations written before the prepared marker was introduced
remain readable and replayable: their exact manifest and pairs are verified,
and a marker is not required or backfilled. For an old never-published stage
whose entire manifest was already deleted and which never had a marker, there
is no surviving generation-level evidence distinguishing it from a never-created
stage. Content-addressed pairs are shared across revisions and cannot prove that
history. New stages atomically create the marker, closing that boundary for all
new writes; this is not a retroactive integrity claim about deleted old evidence.
Valid never-published stages remain retryable. After verification, one conditional Update atomically
publishes every role pointer with the resource revision and integer RDF input
position stored in its generation manifest. Older concurrent publishers cannot
overtake newer revisions; same revision with different content/position fails.
Failure after complete staging but before publication leaves no active graph
and permits identical replay. A corrupt existing pair fails instead of being
overwritten. Remote success followed by PG rollback is reconciled through
verified remote replay. This is not a distributed PG/Jena transaction.

The global PG projection watermark advances only across the contiguous
completed/classified outbox prefix. `projection_classifications` is durable,
append-only evidence with kind/version. `advance_contiguous(limit=1000)`
catalogues only the exact foundation event kinds in `nonsemantic`; unknown
events and semantic events missing originating registration block. Pending or
failed earlier semantic positions block later successes. Explicitly classified
worker audit events prevent an outbox feedback loop; classification itself
does not append ledger events. The resource RDF input position is not mislabeled
as this global contiguous watermark. Maintenance may call `advance_contiguous`
again to consume bounded batches of nonsemantic positions.

Inference dirty state is recorded **in the command transaction**, before
asynchronous projection. Until dependency indexing exists, `inference_fence`
conservatively invalidates all current derived results.
`inference_is_current(uow)` is false while expected/resolved positions differ.
S04/S05 consumers must enforce that fence before exposing current conclusions;
physical historical derived graphs are not proof of current validity.
Successful projection creates an idempotent durable `inference_requests` row.
S04 must consume it, compute from a consistent authorized/versioned input set,
and advance resolved state only after proving all required input positions.
S01 neither registers a fake inference job nor clears the fence. No governance
support rows are invented here; those belong to the later domain/inference tasks.

Migration `S0101_semantic_projection` follows the already applied
`0005a_approval_footprint_seed`. No earlier DDL is rewritten. Runtime has
SELECT/INSERT only on classifications and inference input evidence; operational
projection/fence/request tables allow bounded service updates.

## Source provenance and verification

| Installed source | SHA-256 |
| --- | --- |
| semantica/ontology/ontology_validator.py | `4e3679dcf042d123f77bee2a4ad00aa581972f24738000cae1d9dac07111997f` |
| rdflib/term.py | `564280e97c4e64b5b704bd1fff96ebc374ebd2dcc2df134cda62452a3f83c1db` |
| rdflib/plugins/serializers/jsonld.py | `e2ca4671dcd76d30d60cb309d87385b2a9929c5b869ff902cfacfcb9c3cb0730` |
| rdflib/plugins/parsers/jsonld.py | `0fff4c97523ef4770c7bea8de748ca36ee30782eb30cdcf6cfea7fe463174bec` |
| pyld/jsonld.py | `97a88014435cde5d142d076127bd7e4970f64ba77433fa47797523eff2e62bda` |
| pyld/canon.py | `bda563dba000ab7a7d153e9aa732a269bd4fe12f2344000aca9295b37669a922` |
| pyshacl/entrypoints.py | `2c4fc89cb9d4ea7c17d4417661e53b60108efc2c5d2872f0e7ea2ce9c6d9122f` |

Semantica release tag commit:
`2a9afec685fe6f4a18350834caed3d6a9affb27a`; wheel SHA-256:
`faf27d6259ae813ddb44c5213e2f8867592f27e6e3d6b47cea10f1844513799f`.
PyLD wheel SHA-256:
`58aa63542a5989431426bdfef932db0d1b974e2928e179f74d5a903804de9692`.
The application lock includes the actual mandatory Semantica closure, not a
`--no-deps` installation. Windows Python 3.12.7 `pip check` passes.
Linux closure remains a separate installation gate.

Primary references: [Semantica 0.7.0](https://github.com/semantica-agi/semantica/releases/tag/v0.7.0),
[PyLD 3.3.0](https://github.com/digitalbazaar/pyld/tree/3.3.0),
[Jena value canonicalization](https://jena.apache.org/documentation/tdb/value_canonicalization.html),
[SHACL](https://www.w3.org/TR/shacl/).

Acceptance files are `tests/integration/test_semantic_adapters.py` and
`test_projection.py`. `--check-persisted-rdf` selects only the dedicated
previously seeded persistence test; that mode performs remote reads without
reseeding or other test writes. Run it after an actual owned service restart.
Never substitute a fresh seed for restart evidence. Compose equivalents:

```sh
docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_semantic_adapters.py tests/integration/test_projection.py -q
docker compose -f infra/compose/compose.yaml restart fuseki
docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_semantic_adapters.py -q --check-persisted-rdf
```

These container commands remain mandatory pending gates, not successful native
results. Companion storage, duplicate SHACL work and reconstruction cost must
be measured in D04; no performance/capacity or complete OWL 2 DL claim is made.

JSON-LD input must be an inline object/array. A bounded public PyLD expansion
with an always-denying document loader rejects an explicit named graph
`urn:x-rdflib:default`, including compact aliases, before RDFLib can conflate it
with its default-graph sentinel. External contexts/imports are rejected before
expansion, and child-process network denial remains in force throughout.

Known upstream warning limitation: the pinned public RDFLib `from_rdf`
serializer calls deprecated `Dataset.contexts` and `Dataset.default_context`
inside `plugins/serializers/jsonld.py` lines 164, 171 and 173 (source hash above).
The acceptance export tests expose these warnings; no broad or narrow warning
filter hides them. Our Turtle parser uses current `Dataset.default_graph.parse`.
Upgrading or bypassing the pinned serializer merely to hide these warnings
would require a new lexical-fidelity verification.

The genuine Semantica JSON-LD path additionally reaches pinned RDFLib
`plugins/parsers/jsonld.py:159`, which constructs a deprecated ConjunctiveGraph
for Semantica's Graph sink. The direct in-process missing-dependency/mismatch
test exposes this twice. This additional upstream warning is visible and
documented, not suppressed or treated as pre-approved by the earlier warning
deferral. Normal validation still runs inside the bounded child process.
# Trusted focused SHACL invocation

S03 may pass internal `focus_iris` to the existing S01 adapter. The original
published Shape bytes are verified against the bundle digest and prepared with
the original node-identity mapping first. Inside the bounded child, each
original root target set is intersected with the requested subjects. Only root
target predicates change in that invocation view; constraints, nested shapes,
lists, severity and shape identities remain intact. Genuine Semantica and the
companion receive the same view. This is not direct pySHACL `focus_nodes`,
which would omit required nested checks with the installed dependency.

Supported targets include targetNode/Class/SubjectsOf/ObjectsOf, target-bearing
PropertyShapes and direct `rdf:type rdfs:Class` implicit roots. Shape-side
subclass class-marker inheritance is explicitly unsupported rather than
silently incomplete. Equivalent applicability can use explicit targetClass.
Focused reports record `focus_iris`, `invocation_scope="focused"` and
`original_shapes_digest`; conformance describes that invocation, not the whole
dataset. Unfocused callers retain their original behavior.

S03 gathers complete necessary context along actual nested shape constraints,
with finite node/depth/work budgets and the existing adapter byte/time budget.
A plain class check uses independently authorized confirmed type provenance;
explicit nested constraints load their necessary full fact context. Hidden or
incomplete necessary context fails closed; unrelated related-object roots do
not become an unbounded validation closure.

The optional trusted `root_selection` maps focused IRIs to original prepared
root IDs. Unknown IDs, focuses outside the invocation and roots not actually
applicable to their focus fail closed. Secondary affected nodes can therefore
evaluate only their affected root-shape pairs without dropping any nested
constraint. The report includes that selection; no HTTP caller may supply it.
S03 additionally rejects repeated active node/shape ancestry as incomplete
context, while allowing finite recursion and ignoring genuinely deactivated
shapes. This is an S03 supported-profile boundary; unfocused S01 behavior is
unchanged, and recursive SHACL semantics are implementation-defined.
