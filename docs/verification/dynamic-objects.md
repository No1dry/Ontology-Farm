# Dynamic objects, support and geometry contract

S03 adds migration `S0103_objects_facts_geometry` after
`S0102_ontology_releases`. Existing S01 tables and immutable ledger events remain
in place. Object, assertion and support IDs are distinct; object IRIs are stable
`urn:lzo:object:{UUID}` values. Removing support never deletes the entity or its
history, metadata, other supported facts or platform notes.

The `/api/v1/objects`, `/links`, `/validation` and `/geometry` routes expose the
DTOs in `lzo.semantic.schemas`. Writes require the shared session/CSRF and
idempotency controls; mutations require `If-Match`, and a duplicated body
revision must agree with it. `register_object`, `apply_fact_patch` and
`write_geometry` use the caller's UoW and never commit. A composed failure rolls
back the object, facts, geometry, revision and semantic outbox together.
Successful writes return a real immutable revision and pending projection.
S03 rechecks that revision through the existing current-rights history reader
before committing or returning a cached idempotent receipt. Revoking a reference
used by the original event hides its replay even when current business content
is independently visible; it never re-executes the cached effect.

The common boundary verifies the entire persisted execution bundle, the active
published release, original published Shape digest and actual immutable
configuration versions. Historical forms and bundles remain readable but cannot
authorize an obsolete write. Object forms use the current published Schema;
no fixed agriculture field list exists in the API.

## Authority and evidence

The existing `owner_policy_iri` supports four generic values:
`urn:lzo:authority:ontology_owned`, `source_backed`, `derived`, `read_only`.
Only the actual genesis `authorized_manual` authority snapshot allows manual
ontology-owned properties, ordinary declared relationships and geometry.
Source-backed accepted writes require the trusted `AcceptedDecisionPort`;
derived/read-only writes and unknown authority policies fail closed.
`AuthorityPolicyPort.resolve` takes precedence over genesis and must resolve
the specified version. Neither port is selectable by a request.

`AcceptedDecisionPort.verify(uow, actor, decision_ref, effect, bundle)` verifies
the exact create/patch/geometry effect, current original actor rights, current
authority, immutable evidence, baseline and independently accepted decision.
Its `DecisionEvidence.references` includes the actual decision revision and
all protected pinned revisions. No production I02 adapter is provided by S03.
The integration fixture uses real PostgreSQL evidence and F06 independent
review, including missing/random/unrelated/stale/revoked negatives; this proves
the integration boundary, not completed I02 governance.

Exact RDF lexical values, datatype and language remain separate from raw source
values and normalization. Multiple support IDs may support the same assertion.
Withdrawals require the same current field/source authority as additions.
Accepted and candidate projections and their validation reports are separate.
Each report persists its own context heads, type declarations, protected
references, target applicability and time boundary. Candidate-only changes or
expiry omit the stale candidate report independently of a still-current
accepted report. Historical events without candidate evidence do not borrow
accepted evidence. This does not weaken full content or historical permissions.
Manual acceptance never upgrades `data_nature` to `verified`.

All full object, list, geometry and history reads retain ALL original protected
dependencies. A dedicated immutable type declaration permits narrow confirmed
type context without disclosing full creation/history values. Manual declarations
pin their Schema/class dependencies; imported declarations pin actual decision,
mapping and source evidence. A trusted verifier may supply `type_references`
only as a verified subset of its complete references; absent a partition, the
complete decision closure is retained. Explicit nested SHACL constraints require
their full necessary authorized fact context.

Reverse upstream witnesses used only for another object's validation are kept
separate from business content dependencies. Successful validation events and
their outbox payloads retain every such protected reference. A current content
read may omit a hidden or stale report and return `pending`; it does not disclose
the hidden references. Historical event reads remain protected by their original
complete dependencies. A pure withdrawal which cannot revalidate a hidden
upstream does not copy that old report or its validation-only references into
the new event. Original source, type, Schema and forward-context restrictions
are unchanged.

## Validity, lifecycle and validation

Current facts require nonwithdrawn accepted support and
`valid_from <= server_as_of < valid_to` (an absent end is unbounded).
Reads filter time independently of workers. Future/expired assertions remain
available as history/evidence, and candidate assertions never become current by
age or by projection success.

The existing F05 `enqueue_at` schedules the next boundary using its ordinary
available-at/claim/lease/current-original-actor authorization. Each object
revision pins an origin event; superseded jobs have no effect. A due job uses
`ctx.batch`, rechecks current support and active bundle, appends immutable
evaluation-time/materialized facts, and schedules the sole S01 projection
path. No direct second RDF writer or polling scheduler exists.

`ObjectView` includes `evaluation_time`, `next_transition_at`,
`validation_state`, `validation_report` and `candidate_validation_report`.
Expiry or authorized last-support withdrawal can make a formerly active
object need review; it cannot retain a false value to satisfy cardinality.
New active creation/accepted additions still enforce strict constraints.
An overdue transition with unavailable/revoked worker authority is visibly
pending; it cannot extend truth. S04 proofs and S05 caches must independently
honor these temporal boundaries. S01 dirty inference fences remain unresolved
until an actual inference producer completes them.

`fact_validation.py` constructs bounded context and evidence; S01 remains the
only SHACL executor. Original `targetObjectsOf` incoming witnesses are loaded
before root applicability, with the proposed subject facts replacing its old
facts. A changed target also triggers a bounded reverse traversal of direct
paths and nested/logical/qualified shape dependencies. Secondary nodes evaluate
only affected root-shape pairs, including every nested constraint of those
roots. The primary subject retains all its applicable roots. Literal or
unregistered vocabulary targets in affected objects-of constraints return a
typed unsupported-profile 422. Context limits are 10,000 loaded nodes, 20,000
traversal steps and depth 32; an incomplete traversal cannot report conformance.
An actual engine evaluation which repeats the same data-node/shape pair in its
current ancestry returns `VALIDATION_CONTEXT_INCOMPLETE`. This is this platform's
finite recursive-evaluation boundary; SHACL leaves recursive semantics to
implementations. Finite chains through different data nodes, shared completed
branches, deactivated shapes and ordinary non-recursive RDF cycles remain
eligible. Pure withdrawal/expiry still converts incomplete validation to pending.
Reverse traversal first filters candidates by actual structural root
applicability, never by the caller's visibility. Non-applicable hidden candidates
are neither disclosed nor added as dependencies. Actually applicable hidden
roots and paths fail with an opaque 404.

The trusted S01 `root_selection` map uses root IDs prepared from the original
artifact and focus IRIs. Unknown, non-applicable or out-of-focus selections fail
closed. The original digest and shape identities are verified before deriving
root targeting; constraint bodies remain intact. Genuine Semantica and the
exact-report companion consume the same invocation view.

Each current report records the exact bundle, evaluation time, complete fact
context heads, independent type declaration records, all original objects-of
predicates (including absent ones), incoming witness stamp and earliest
necessary future start/end boundary. A bundle, dependency head, type evidence,
applicability, time or current permission mismatch returns `pending` with no
current report. A full context head change conservatively invalidates the
report even if only an unrelated field on that necessary object changed.
Type-only declarations remain independent of unrelated field revisions.
These are derived validation checks; they create no other object's business
revision and no new queue.

Disjoint qualified cardinalities traverse sibling qualifiers on the current
property's value nodes, as defined by their shared `sh:property` parents.
The sibling property's path need not have values. Those potential edges retain
complete forward evidence, authorization, pins and reverse impact under the
context budgets. They do not prove that the engine actually evaluated a sibling.

S03 alone requests the trusted `finite_evaluation` adapter mode. Inside the
isolated child, it temporarily wraps genuine pySHACL `Shape.validate` to track
active exact RDF node/original shape pairs, resolving targets through the engine
and evaluating multiple targets separately. It retains the invariant checks in
`ConstraintComponent.recursion_triggers` but disables that shape-only heuristic's
early truncation. All constraints remain genuine, including conditional sibling
evaluation. Both hooks cover the public Semantica and companion passes and are
restored in `finally`. Default S01 calls keep their existing engine behavior.

This internal compatibility boundary requires pySHACL 0.30.1 and exact SHA-256
hashes of `inspect.getsource` for `Shape.validate`
(`e7e61df1a42f5bf307b1c6280074eacb77eaa38cf60112a02c247bce359d80f0`)
and `ConstraintComponent.recursion_triggers`
(`5c1095f4858bbc492cf7b5bc82802dabf30d4ed75b0fdbed0fc2279dc3c9199e`).
An incompatible version/source fails as typed unavailable dependency. Dependency
upgrades require explicit compatibility review. No installed package is edited.
Actual evaluation retains the pinned depth limit 15, adds a 20,000-entry limit
across both passes, and keeps the existing child time/memory limits. Finite does
not mean unbounded: an eight-node property-shape chain and its terminal violation
are covered within budget; deeper exhaustion is typed incomplete. Reached
recursion is transported as `SemanticRecursionIncomplete`; additions fail with
422 `VALIDATION_CONTEXT_INCOMPLETE`, while pure removal remains pending.

The existing-subject `/validation` response aggregates the actual focused
invocations, retaining their original Shape digest, evaluation time, focus
union and selected secondary roots. A node checked with all applicable roots
remains unrestricted in the aggregate selection. Empty input has an empty
focused scope; it does not certify dataset conformance.

Known bounded-executor input/output, time/memory, transport and unavailable
dependency failures carry `SemanticExecutionIncomplete` subtypes while
preserving their existing ValueError/RuntimeError/ImportError compatibility.
S03 catches this type only at the validator call boundary. Pure withdrawal and
due materialization keep removed truth absent and the unavailable report
pending, including candidate revalidation. Additions fail with typed 422
`VALIDATION_EXECUTION_INCOMPLETE`; unrelated programming errors still fail and
roll back the operation.

Deactivation requires real independent F06 approval and preserves all identities
and evidence. Other active subjects' nonwithdrawn accepted current or future
inbound links block it; expired/candidate links do not. Self-links retire with
their object. Hidden impact is a generic 404, visible blockers a typed 409.
No implicit cascade or source-support reassignment exists. The existing ledger
serialization ensures concurrent link creation rechecks target lifecycle.
Ownership transfer remains a protocol in `semantic/ownership.py`, with no
working endpoint or invented grant effects.

## Geometry

Successful geometry preserves original JSON, source CRS, raw support and
decision in immutable history, independently from normalized PostGIS geometry.
Deployment registration initially supports EPSG:4326, EPSG:3857 and EPSG:4490;
`register_crs` can extend this trusted policy. Coordinates use explicit x/y
order and two dimensions. Geography area is reported in square metres with the
actual transform/area method, never square-degree area.

Missing/unknown/conflicting CRS, malformed/nonfinite/out-of-range, empty and
topologically invalid geometry return typed 422. Empty geometry is explicitly
rejected even when PostGIS reports topological validity. There is no automatic
repair or success effect. I04 owns retained original snapshot/staging and
failed-row evidence; S03 does not commit a second quarantine workflow.
GeoJSON features filter current visibility before paging and include
object_id, geometry_revision, data_nature and source_crs. Map geometry does not
grant access or change owner_scope_id.

Native real-service evidence is separate from the mandatory pending
Linux/Compose, offline and capacity/benchmark gates.
