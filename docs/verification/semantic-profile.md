# Governed ontology profile

S02 adds immutable ontology releases and execution bundles to the S01 exact
logical RDF contract. Migration `S0102_ontology_releases` follows
`S0101_semantic_projection` on the single migration chain. It introduces no
ingestion, assertion, conflict, or inference-domain substitute tables.

## Definitions and import

`ontology.schemas` contains the public `SchemaDocument`, class, property,
relation, draft, release, impact, form, inheritance, diff, import and axiom DTOs.
Unknown request fields are rejected. Existing-resource HTTP writes obtain
their revision exclusively from the quoted `If-Match` header. The service
`SemanticDraftImport` retains `expected_revision` for transactional callers
such as I04; its HTTP body is `SemanticDraftImportBody`, without that field.
New imports use revision zero. Uploading source evidence advances the F02
draft head, so clients reload the current draft revision before importing.
The import operation documents `If-Match` as its sole HTTP revision source,
required conditionally when `target_draft_id` is supplied.

Turtle and JSON-LD use the S01 bounded, network-denied RDFLib Dataset parser,
canonicalization and document-scoped blank-node mapping. Every named graph is
inspected and indexed; its original artifact, graph names, support report,
canonical digest and reconstructed quads remain in immutable draft revisions
and release snapshots. Import does not publish. Original bytes remain in F04
even when inspection rejects an unsupported axiom. Authorized profile errors
include graph/subject/predicate/object locations. The diagnostic exception is
a trusted application type, never a caller-controlled error flag.

Supported OWL/RDFS schema statements are class and datatype/object-property
declarations, labels/comments, subclass/subproperty, domain/range, inverse,
symmetric/transitive declarations and deprecation. This is a finite explicit
profile, not OWL 2 DL. Restrictions, equivalence, property chains, arbitrary
extension axioms and anonymous class expressions are rejected with locations.
Unmodified imported language-tagged labels remain exact logical literals.
Explicitly edited DTO predicates replace their corresponding imported values;
removed definitions lose their old subject statements in the new release.
Older releases and original source files remain unchanged.

External JSON-LD contexts/imports and all unresolved `owl:imports` are rejected.
S01 currently does not resolve even a local `owl:imports` statement; dependent
definitions must be supplied in the offline document and accompanied by
registered immutable dependency artifacts. An artifact reference is checked
against its F04 version, size, media type, digest and current authorization.
Missing parents, relation endpoints/inverses or superproperties, duplicate
definition IRIs and inheritance cycles fail closed.

SHACL supports S01's explicit Core whitelist: types, counts, ranges, enums,
patterns, endpoints and the documented advanced Core constraints. Executable
SPARQL/JavaScript/rules, unknown extensions and complex paths are rejected.
There is no remote fallback or arbitrary uploaded execution. The same byte,
quad, blank-node, child-memory and timeout budgets apply. Budgets are
operational limits, not supported farm/device/source-type counts.

## Forms and effective Shapes

Forms combine target-class property shapes across the complete explicit
multiple-inheritance closure. The result records inherited origins, required
counts, maximum counts, datatype/reference classes, unit, exact RDF enum
terms, read-only hints and widget metadata. Incompatible counts, datatypes and
disjoint enumerations are rejected. UI hints do not grant permissions.
The optional `ui_metadata.fields` value and every field entry must be JSON
objects; malformed values return a typed 422 before any draft revision is
persisted, including entries not currently used by a Shape.

`semantic_profile.effective_shapes` compiles property datatype/enumeration and
relation range constraints onto their declared direct-path fields. Canonical
Shape bytes are deterministic; the stored Shape artifact and execution
bundle bind those exact bytes. The original Shape input is retained separately
in the approved document/source artifact. Consumers must load the published
Shape artifact rather than substitute raw draft text when validating a bundle.
Empty constraints are stored as an explicit harmless Turtle comment.

## Independent governance and complete impact

Creation/edit/submit requires `schema.edit`; independent decisions require
`schema.review`; installation, queue control and publication require
`schema.publish`. Schema governance uses explicit global grants in security
class `schema`. These permissions do not imply one another or generic `edit`.
Every protected source, artifact, baseline and affected resource must also be
currently viewable. A hidden impact returns a generic 404, without names,
counts or a filtered report presented as complete.

The trusted `schema.publish` resolver reads actual draft/base content, the
catalog revision, registered authoritative S01 input generations (including
pending projection), and their exact protected dependencies. It validates
accepted logical statements with genuine Semantica and the S01 typed-report
companion. Only explicit subclass axioms contribute type closure; candidate
domain/range statements never manufacture confirmed instance types.
Unknown registered input producers prevent complete impact resolution.
S03 and later producers must continue the S01 complete-reference contract;
S02 does not invent a future instance/conflict store or claim those domains
are already implemented.

The canonical approval command contains `operation="ontology.release"`, draft
identity/revision, document/import digests, and a server-computed impact digest
covering actual baseline and impact. Neither risk, baseline nor impact is
trusted from request flags. Preview returns violations. Submission/execution
of incompatible Shapes requires a reviewed migration plan with description,
validation and rollback fields, and actual authoritative input must already
conform. A migration plan alone cannot override a violation.
Effective metadata risk and form/permission/migration comparisons use the
current PG active immutable release that activation would replace. A declared
base remains historical source provenance; it does not define the effective
change when another release has become active. An old-base label edit that
removes current structure therefore requires independent high-risk review.
Intentional reviewed rollback remains supported with the current catalog
baseline bound throughout preview, approval and installation.

S02 owns the current `metadata.update` trusted policy discriminator. Only
label/description edits on existing definitions with all other authoritative
document/import fields unchanged qualify for F06 low risk. Structural
changes fail that path and require independent review under the system floor.
An unchanged document is not a metadata shortcut: a deliberate new snapshot
still uses high-risk publication and independent review.
Future domains must explicitly extend one trusted dispatcher rather than
silently overwrite this policy registration.

## Installation, retries and active state

Publication first rechecks F06, stores immutable OWL/Shape/UI artifacts,
creates the durable execution bundle and release snapshot, appends
`schema.install`, and schedules the sole S01/F05 `rdf.project` outbox path in
the same caller-owned UoW. HTTP returns a 202 JobRef, not synchronization.

`register_projection` now accepts deployment-owned optional
`before_publish(uow, actor, immutable_revision_row)` and
`after_publish(uow, actor, immutable_revision_row, receipt)` hooks. Existing
producers need no hooks. S02's pre-hook re-resolves F06 and current inputs
inside the fenced worker batch before a remote write. Its post-hook activates
only a verified `synced` receipt. Failed/superseded receipts cannot activate;
failed gates are recorded without producing a remote effect. Worker commit
still owns the entire PG batch and checkpoint.

Activation advances the bound draft revision and catalog revision in the same
transaction as the published state, consuming the approval baseline exactly
once. The prior release becomes deprecated but its artifacts, document,
axioms and bundle remain readable under current rights. Competing catalog
baselines cannot activate out of order. Failed/paused/cancelled attempts permit
an explicit new draft revision; stale retries cannot publish that new draft.

Remote success followed by PG rollback leaves a staged/installed immutable
generation, with no PG active switch. Identical replay verifies that generation
and performs one activation. Public consumers must choose the PG active,
published release; arbitrary remote graph presence is not activation.
`releases.get_released_form_schema(uow, actor, release_id, class_iri)` is the
shared form-consumption boundary used by HTTP. It checks current visibility
first, then permits published and historical deprecated versions; installing
or failed versions return `409 SCHEMA_NOT_PUBLISHED`. The draft-form endpoint
is the explicit preview. Reading a deprecated form does not authorize new
writes against an obsolete bundle; active-bundle write validation belongs to
the consuming domain.
There is no distributed PG/Jena transaction and no administrator worker
fallback. S01's inference fence remains dirty until the real S04 consumer
resolves it; S02 does not pretend inference has completed.

## Artifacts, history and bundles

F04's standard upload resolves its action from authoritative resource kind:
`schema.draft` uses `schema.edit`, all other targets retain `edit`.
Pre-stream authorization, registration and recursive evidence attachment use
that same policy with fresh current rights. Generated release artifacts use
the internal `register_schema_artifact` wrapper, fixed to `schema.publish`,
only for a real newly prepared release by its initiating actor.

Both paths reuse F04 quotas, immutable versions, idempotency and append-only
history. S02 distinguishes two server-derived closures at publication:

- Definition/source provenance: the draft's actual imports, declared source
  dependencies and base-release provenance, with immutable artifacts and
  configuration versions. Public release/form/generated-definition-artifact
  and default/additional bundle reads require every such dependency.
- Governance impact: all current authoritative affected-object references,
  the independent approval and complete baselines. Publication job/control/
  execution references and private preparation/install history retain this
  complete closure. Full Schema diff additionally checks every stored impact
  reference before returning its report.

An incidental Farm B object assessed during release governance does not hide
shared definitions from a Schema viewer authorized only for Farm A. That user
still cannot read the private impact, approval, job or impact-bearing history.
No partial impact report is presented as complete. Imported or declared source
content is never declassified by this distinction.

Immutable release and bundle source dependencies are authoritative when
flattening subsequent source provenance; their private governance-history
references are not reintroduced as incidental sources. Existing stored
dependencies are never removed or rewritten. Public publication-history
events contain only the bundle/digest and retain source protection; events
containing private impact references retain the full governance closure.
The server-owned `definition_source_closure` entry point reuses the same
flattening implementation while excluding catalog lifecycle traversal.
Draft creation no longer records the catalog as a definition source; genuine
declared base/import/source dependencies remain. Catalog state and activation
history still participate in the unchanged governance closure and complete
approval/job references. Thus an independent base-less, source-free release
does not inherit a previous release's private source through catalog history.
Stored historical dependencies are not rewritten or automatically removed.

F04 artifact reads check all immutable originating-event dependencies before
metadata/content or operation permissions. Release/draft lists and F04 history
filter visibility before pagination. Source-only revocation therefore hides
derived files/history even while their parent resource still has direct view.
This is dependency evidence, not a second grant system or recursive read-time
authorization engine.

The first publication writes persistent default authority, ruling and finite
inference configuration versions through ordinary F02 revisions. Defaults
allow authorized manual platform-owned values, retain external values as
candidates pending deterministic rules/independent review, prohibit automatic
identity claims/conflict overwrite, and require explicit axioms for the six
finite inference families. Bundles pin their immutable resource identities and
the actual stored Shape artifact digest. I05 can later create a new bundle
with new version identities; it cannot mutate these snapshots.

`schema_axioms` identifies each published supported triple with the immutable
schema-artifact digest plus the canonical exact logical triple. Import graph
names and display labels are never axiom identities.

## Verification boundaries

`tests/unit/test_semantic_profile.py` and
`tests/integration/test_schema_releases.py` exercise the real parser/validator,
PostgreSQL, F04 filesystem artifacts, HTTP session/CSRF contracts, queue and
Fuseki repository. The S02 task report records exact RED/GREEN commands,
runtime migration/restart evidence and outstanding gates. Native execution is
supplemental; Linux/Compose, container persistence, offline delivery and the
specified 8 CPU/32 GB benchmark remain independent required acceptance gates.
