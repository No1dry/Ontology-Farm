# Governed imports

`POST /api/v1/imports/preview` accepts a source, immutable registered artifact or
completed authenticated scan, exact parser options and current execution bundle.
It returns a 201 preview or a 202 planning job. A planning job's `result_ref`
points to the final immutable preview. Preview rows are paged at
`GET /api/v1/imports/previews/{id}/rows`; the report includes only the first page.
Previewing persists raw evidence and planning results without accepted objects,
assertions, geometry, semantic events or projection jobs.

`POST /api/v1/imports/apply` binds the preview revision, input digest and applicable
approval. Its job ID identifies `GET /api/v1/imports/{id}` and `/rows`. An approved
preview has one execution job. Each row commits its verified decision, complete
S03 object/fact/geometry effect, source supports, event and worker checkpoint
atomically. Execution rechecks current rights, source evidence, configuration and
heads. A resumed run can account only for its exact event-proven committed rows.
Failed and review rows remain located in the immutable report and run results.

Ordinary source-backed instance effects need a real F06 low-risk authorization
under the reviewed published admission policy. Important or unclassified effects
require independent approval. Source eligibility does not itself authorize a
batch. Schema and Shapes selections create or edit S02 drafts under current
draft revision/edit permissions; actual publication is a separate governed action.

## Semantic files

For Turtle or JSON-LD instances, set `semantic_format` and `semantic_kind` to
`instances`. Optional `file_options` uses `SemanticFileOptions` and its format
must match `semantic_format`. Structured file options retain their existing
contract. Schema/Shapes selections reject file options. Source configuration
determines the registered data nature; caller metadata cannot promote it.

The offline bounded S01 parser groups RDF by exact subject and original graph.
Each `semantic-v1` raw record preserves the subject, default/named graph, all
predicate values, exact lexical form, datatype/language, canonical and original
digests, and original BlobRef. `predicates` uses full predicate IRIs; `terms`
contains the same arrays keyed by `sources.digest(predicate_iri)`. Blank nodes
retain source, artifact version, canonical document and local identity scope.
They are never accepted RDF terms until a governed identity mapping resolves
them. `owl:sameAs` is an alignment proposal, not an automatic identity merge.
Every unsupported predicate has a located review disposition. Remote JSON-LD
contexts and automatic OWL imports are forbidden by the shared parser.

An explicit semantic temporal field uses `terms.<predicate-digest>`, for example
`temporal_fields.valid_from_path`. The configured path must exist and contain
exactly one literal with a valid timestamp. Offset-free timestamps require an
explicit `source_timezone`; ambiguous/nonexistent local times are rejected.
Missing, multiple, nonliteral or malformed values are invalid, never silently
unknown. An omitted path retains an open interval or unknown observation. Receipt
time never supplies a business timestamp. Authenticated raw `temporal_paths`
records these metadata dispositions while retaining the exact original terms.

New acyclic references use bounded complete S03 prefix validation in a rollback
sandbox. Existing and self references undergo ordinary semantic checks. New
mutual cycles, invalid predecessors and overlapping targets remain explicit
excluded rows. Resource capacity exhaustion is retryable incomplete planning.

## Completion and current reads

PG commitment does not imply projection or inference completion. Projection
retries consume the same committed RevisionRef without repeating source effects.
An import is complete only after its required rows succeed, base projections
sync, and actual published inference coverage accounts for its committed input
events. Compute-job success alone is insufficient. Later unrelated global
dirtiness does not erase historical import completion; current query readiness
still uses the independent Dataset/proof gate.

Active object identity is separate from eligible current type evidence.
Invalidated source type evidence can yield an empty current class list while
independently eligible manual facts and geometry remain readable. Original type
declarations and decision provenance remain in history. No manual type authority
is inferred from an unrelated manual fact.

Configured geometry paths must resolve every segment and contain the final key.
A missing path or a non-object intermediate value is invalid; an explicit final
null withdraws only that source's exact geometry support. Authenticated source
deletes withdraw their own support even when tombstones omit geometry fields.
Without a configured path, a positive normalized file geometry is used; absent
normalized geometry leaves existing geometry unchanged. Equal normalized source
geometry preserves an existing independent manual selection while retaining the
new source support and decision. Different geometry retains ordinary conflict
review and field-ownership checks. Replacement classification retains unretired
adopted geometry and its decision history even when changed raw evidence hides
that support from current reads. A material change requires an independent row
decision; batch approval does not supply it.

A complete row that only withdraws exact fact or geometry supports can commit
even when the remaining object fails a required-value constraint. Its revision,
support retirement, history and outbox remain atomic, and the object identity
survives. Preview retains the actual quality violations; execution records the
nonconforming report. Bounded incomplete validation remains pending with no
fabricated report. New facts and geometry replacements require ordinary final
state validation and cannot use this withdrawal exception.
