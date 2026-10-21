# Finite inference and proof generations

S04 provides internal services. The public proof and graph query routes belong
to S05 and are not assembled by this change.

## Version selection

`ontology.current_execution_bundle` resolves the single authoritative
`ontology_catalog.active_bundle_id`. S0104 backfills that pointer from the active
release's original bundle. Schema installation changes release and bundle
atomically. Object writes, deactivation, temporal jobs and validation freshness
use the same selection.

The immutable S02 genesis profile remains disabled and unsupported. Internal
`preview_inference_profile` and `activate_inference_profile` require an actual,
independently reviewed F06 approval binding configuration and current baselines.
Activation creates a new immutable configuration and bundle, appends catalog
history, and produces a dirty-rules projection through S01. Later schema
publication carries the explicitly selected profile forward. Old bundles and
configuration histories remain readable under current permissions.

The supported `finite_explicit_axioms_v1` profile executes only subclass
transitivity, type propagation, subproperty propagation, inverse, explicitly
declared symmetry, and explicitly declared transitivity. Domain/range does not
repair endpoint types. Exact RDF lexical terms remain distinct.

## Roots and proofs

The pure engine accepts current accepted assertions with actual active supports,
explicit schema triples, and typed `TypeDeclarationEvidence`. A declaration
retains its real creation `RevisionRef`, class, creation bundle and data nature;
the observed lifecycle revision is a separate eligibility pin. Inactive
declarations are ineligible. No declaration is converted into a fabricated
assertion or support.

`SchemaAxiomEvidence` binds the actual S02 registry IRI, exact triple, release ID
and schema artifact SHA-256. A persisted scope rejects absent, mismatched or
unbound evidence. Pure standalone inputs may use explicitly distinct
`urn:lzo:logical-axiom:` references; those are not registered evidence.

`ProofNode` contains real immutable assertion, declaration, proof and schema
axiom identities. The first deterministically discovered derived witness is a
canonical anchor, so cycles do not enumerate infinite paths. Each
`premise_conclusions` group is AND-required; matching roots and stored proofs
within a group are OR alternatives. Stable derived assertion IDs use the exact
conclusion; proof IDs bind the grounded application and execution bundle.
`ProofNode.active` records eligibility in that immutable generation, not a
mutable claim about today's facts. Current consumers must use the manifest,
root-validity and permission gates; they must not reuse that historical flag as
a currentness test.

`select_witnesses` is an internal bounded expansion seam. Callers must first
select eligible authorized roots and authorize schema input. It returns
`SelectedProof` and `SelectedPremise` witnesses separately from the immutable
canonical record, omits unsuccessful or hidden branches, preserves data nature,
and reports truncation. S05 must not expose raw canonical records when only a
different witness path is authorized. `historical_proof` instead applies F03's
current ALL-reference history protection; historical existence does not assert
present validity.

## Scope and publication

`recompute_inference(actor, bundle, input_watermark)` enqueues an authorized full
recomputation. Successful base projections call `schedule_inference` under their
original initiator. Initial bootstrap and schema/rule changes require complete
global inventory and authorization. Ordinary edits use the complete old plus
new connected footprint, including removed bridges, prior component membership,
incoming links, active supports and relevant future starts. Hidden dependencies
fail authorization; they never disappear from the inventory.

Immutable `InferenceScope` binds actual heads, dependencies, common database
evaluation time, next validity boundary, authorization version, bundle,
projection frontier, covered input events, prior manifest and input digest.
The worker rechecks current rights and exact managed logical input generations
before computation and again before publication. Every component is recomputed
from eligible base roots, never from a previous derived graph.

Each output has its own managed S01 resource. The trusted producer explicitly
uses `dirty_kind=None`, retaining the standard outbox, exact-term representation
and recovery protocol without creating recursive inference inputs.

PG immutable manifests and a compare-and-swap assembly pointer determine reader
eligibility. Disjoint jobs can merge only while their captured replaced
generations still match. Unrelated components remain opaque; their membership,
heads, bundle and temporal stamps are checked without repacking protected
content. An `assembling` manifest can record one complete component while other
components remain stale. It does not expose any globally current derived
closure. The prior complete manifest remains immutable.

Only actually covered input events enter `inference_coverage`. The input and
inference watermarks advance through the verified contiguous projection prefix,
and the dirty fence clears only for covered semantic inputs. A successful
component computation is distinct from successful output projection and global
currentness. `current_conclusions` is the internal ALL-authorized current gate:
it checks the fence, complete manifest, whole membership, heads, bundle, current
database time and exact remote output. Equality of integer watermarks alone is
insufficient, including when an expiry job is delayed, revoked or failed.

The private operational run/manifest tables contain complete inventory needed
for completeness checks. They are not public DTOs. F02 history and F05 payloads
contain only selected authorized evidence plus opaque global commitments.
Retained hidden IDs and counts must not be copied into public history,
checkpoints, receipts, explanations or exports.

## Retry and operational budgets

Wider inference denial leaves a successful base projection intact and preserves
its `inference_requests` row as `pending` with an explicit reason. A queued
computation is a real F05 job, not a new request lifecycle state. Requests become
`resolved` only when their events are covered.

`inference.dispatch` is created from the original authorized trigger for a
transient projection-frontier gap. It pins the original event and bundle and
uses F05's bounded delayed retries. A different actor closing the gap does not
impersonate the first actor. Unsupported profiles and permanent denial remain
honest pending/blocked results; an operator may request a separate recomputation
under their own authority. Duplicate triggers may share only an unchanged real
computation whose immutable scope already includes their dirty events.

An acknowledged output that loses publication remains truthfully synchronized
but receives an immutable `superseded` run disposition, without switching a
manifest or covering inputs. A never-published obsolete or explicitly cancelled
output can receive a separate audited `retired` disposition. This allows the
projection classifier to consume that exact non-input output event; it does not
pretend that RDF was synchronized or that any base input was inferred.

`retire_inference_run(uow, actor, run_id, expected_revision)` requires current
ALL-reference visibility, the run's required action, and `edit` on a data primary
or `schema.publish` on a schema primary. It rejects a still-valid uncancelled
run or an assembly-referenced output. Cancellation proof is nonvacuous: the
computation is cancelled, or its complete expected output set has explicitly
cancelled publication jobs. Immutable terminal records fence computation retries
and publication callbacks. Revocation alone does not withdraw global facts and
does not authorize retirement. An actually authorized operator may cancel the
blocked publication through F05 and retire it under their own audit identity,
then enqueue a new recomputation. Original job initiators and historical
manifests are unchanged. I05 owns later recovery exposure, U04 its actionable
job/governance UI, U06 operational history, and U05 current query eligibility.
Retired or superseded event-to-job bindings stay immutable. Scheduling that
binding again requires current visibility and returns
`INFERENCE_RECOMPUTE_REQUIRED`; uncovered inputs keep that durable pending
reason. Recovery creates a fresh authorized snapshot and real job.

The run's validity window ends at its earliest relevant input boundary.
Persisted component windows use each component's own relevant boundaries, so
refreshing an expired component can retain an unrelated unchanged generation.
The global read gate still requires every component to be current.

The validated settings below are execution budgets, not domain capacity limits:

| Environment variable | Default | Valid values |
| --- | ---: | --- |
| `LZO_INFERENCE_MAX_TRIPLES` | 1,000,000 | Integer ≥ 0 |
| `LZO_INFERENCE_MAX_PROOFS` | 2,000,000 | Integer ≥ 0 |
| `LZO_INFERENCE_TIMEOUT_MS` | 15,000 | Integer ≥ 1 |

Pure `InferenceBudget.timeout_ms=0` and either zero count budget are explicit
incomplete-work tests. Incomplete computation never publishes a partial result
or clears coverage. Cancellation, revocation, stale inputs and failed remote
acknowledgement likewise cannot turn staged output into current truth.

The triple budget bounds the exact union of visited schema, base, type and
derived triples across the entire run. Shared schema and derived triples count
once across components; different lexical RDF terms remain distinct. Capacity
is checked before retaining a new unique triple. Accounting stays private to
the worker and does not add proof/history payload fields. Proof and elapsed-time
budgets also apply across the complete computation.

Cancelling computation alone does not retire already-created publication work.
Cancellation-based retirement also requires explicit F05 cancellation of every
registered publication job. A cancelled computation with no outputs is valid;
independently proven obsolescence remains a separate authorized retirement path.

Fuseki requests reuse a private process-lifetime verified SSL context with the
same certifi trust, hostname checks, `trust_env=False` and request timeouts as
before. No HTTP client or socket is shared. Changes to the installed trust
bundle take effect after process restart.

Native integration evidence is distinct from Linux/Compose, offline and
standard-capacity acceptance. Those external gates remain required.

For S05, `current_conclusions` is an internal ALL-authorized full read, not the
public query adapter. Public reads must reuse or extract its common metadata
current-eligibility gate, then select currently visible, valid roots and complete
witnesses. They must not expose raw component/manifest inventories or require
access to unrelated hidden components when a complete authorized witness exists.
Selected witness choices are a separate display view; immutable canonical proof
edges remain unchanged.
