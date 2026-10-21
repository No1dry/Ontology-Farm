# Three-scene semantic kernel

S06 installs the versioned synthetic vocabulary under
`https://w3id.org/lzo/ontology/`. The namespace is independent of deployment
addresses. `packages/ontology/releases/synthetic-v1.json` declares the explicit
logical inventory; each content file has its role, format, SHA256, size and media
type. The canonical logical digest excludes the index itself and artifact
version UUIDs. The actual registered index and content BlobRefs remain exact
approval dependencies. The seed is **20261020**, and every fixture is synthetic.

`BootstrapPreparation(index, artifacts)` is an internal descriptor containing a
registered F04 index BlobRef and the exact logical-path-to-registered-BlobRef map.
`prepare_bootstrap` validates actual bytes, creates a normal S02 draft, and emits
one immutable `bootstrap.prepared` revision on a `schema.bootstrap` resource.
That revision binds the original draft RevisionRef, document/import digests,
manifest and exact artifact refs. Its current ALL dependency closure protects
association reads and history. The draft's reserved `lzo_bootstrap` metadata is
the approval-covered mirror, never reservation authority. The ledger lock
serializes same namespace/version preparation. Different content conflicts;
same-content retries retain the original bindings. Ordinary draft edits,
including identical-content edits, invalidate the original preparation pin.

Explicit internal `reprepare_bootstrap(uow, actor, manifest,
expected_association_revision)` recovers a preparation only before any real
installation JobRef exists across its association history. It validates and
reuses the original registered inputs, creates a fresh normal draft, advances
the old draft through S02 to invalidate outstanding approvals, and appends an
immutable `bootstrap.reprepared` association revision. The earliest reservation
and all historical pins remain. The fresh draft requires a new normal preview
and independent review. Reprepare never replaces or retries an installation.

`bootstrap_packages(uow, actor, manifest, approval_id)` requires current schema
permissions and delegates to ordinary S02 publication. It returns that actual
schema projection JobRef, including on completed or failed retries. It neither
retries failed jobs nor commits the caller's UoW. The only permitted post-prepare
draft revision advance is the trusted S02 publication event. There is no public
bootstrap endpoint, new approval policy, or storage migration.

Profile activation is a separate S04 preview, independent review and activation
after schema publication. The rules artifact declares the expected finite
profile; its presence never enables inference. Only explicit finite hierarchy,
inverse, symmetric and transitive axioms can participate. The package enables
transitivity for `partOf`; service, location, ownership, monitoring, control and
mounting have separate predicates. `managedBy` requires exactly one Organization
reference, while the registry separately stores one owner scope. A service
relationship grants no permission. All seven device families are declared.

Crop concepts and cultivation cycles are separate classes. Local declarations
align relevant classes with SKOS Concept, SOSA Sensor/Actuator, SSN System and
PROV Entity/Agent through the supported explicit hierarchy. This is not full
standard import/equivalence support; no remote context or `owl:imports` is used.

The integration factory uses real F04 registration, S02/F06 schema governance,
S04 profile governance, public S03 object/fact/geometry writes, real PostgreSQL
and PostGIS, worker projection into Fuseki, public authorized query/proof and
neighborhood reads, and F03 historical proof reads. Scene result fields are
calculated separately from the object validation report, public RDF result,
geometry identity, proof witnesses/source versions and unauthorized neighbor
count. Withdrawal uses the public patch route and worker recomputation.

The component reconstruction case backs up actual test-owned physical RDF,
removes that graph, restores its exact saved triples, and checks graph identity,
public inference/proof and unchanged historical proof. It is component-RDF
reconstruction, not a coordinated PostgreSQL/TDB2 restore or full projection
rebuild. The job inventory is checked to contain only projection, inference and
validity work; no equipment control adapter or Action Engine is invoked.

## Execution evidence

Native interpreter: Python 3.12.7 on Windows. The existing isolated native lane
uses real PostgreSQL/PostGIS and Fuseki; it does not reload, migrate or seed the
populated application. Development runs used the existing sanitized
`.runtime/s04_test.py` wrapper; the approved diagnostic continuation uses the
separately reviewed harness described below. Development evidence is distinguished
from the final frozen-source regression.

Measured service/library versions: PostgreSQL 17.11, PostGIS 3.6.2, Fuseki 6.2.0,
Semantica 0.7.0, RDFLib 7.6.0, pySHACL 0.30.1, SQLAlchemy 2.0.44,
FastAPI 0.141.1 and Pydantic 2.13.4.

- Initial RED: `s06-red`, two failures in 1.64s for missing package index and
  missing bootstrap module.
- Initial package/publication/replay gate: `s06-bootstrap-green`, two passes in
  57.19s. A diagnostic repetition passed one selected test in 57.75s.
- Reservation/current-right/file-integrity gate: `s06-bootstrap-boundaries`,
  three passes in 53.32s.
- Identical-content draft revision RED: `s06-revision-red`, one failure in
  13.16s (preparation incorrectly replayed). After checking the original revision
  and the trusted publication transition, `s06-revision-green` passed both that
  test and ordinary post-publication replay in 73.49s.
- Vocabulary and unavailable-Fuseki recovery: `s06-vocabulary-recovery`, two
  passes in 73.30s. The failed install retained an empty active pointer, and a
  new real Runner instance completed the original schema job.
- First field chain: `s06-scene-first`, one failure in 193.14s. The complete
  package reached the public query but returned `SPARQL_TIMEOUT` under the
  default 10000ms request/15000ms server budgets. S06 thereafter uses an explicit
  test-only 60000ms request/server correctness budget and records request times.
  Production defaults are unchanged. Default-budget usability remains a D04
  performance concern; a larger-budget pass is not latency acceptance.
- A later diagnostic checkpoint passed the seven installer cases and the full
  field chain, including actual withdrawal and preserved F03 history. Four
  public requests took 9.578/9.828/9.500/9.687s with the 60000ms budget. This run
  was interrupted at that checkpoint for the explicit pre-install recovery
  addition; it is not a whole-kernel pass. Instance UUIDs in native tests are
  consistently namespaced per test to isolate the shared Fuseki endpoint while
  retaining shared-device identity within each test.
- Explicit reprepare RED: `s06-reprepare-red`, one failure in 29.29s because the
  named pre-install recovery operation was missing. The focused GREEN passed
  recovery, original installation/replay and independently-valid-parent inherited
  conflict cases: 3 passed, 12 deselected in 148.62s.

The frozen full kernel command was:

```text
.venv/Scripts/python.exe .runtime/s04_test.py s06-kernel-final apps/api/tests/integration/test_semantic_kernel.py -s --tb=short -o faulthandler_timeout=180
```

It terminated with native Windows access violation **3221225477 / 0xC0000005**
after eight installer cases and field passed, during pond's first public query.
The log remains `.runtime/s06-kernel-final.log`; it is not a whole-kernel pass.
The 180s faulthandler emitted a stack trace during field, which continued to PASS;
its pond diagnostic was being emitted when the native fatal exception appeared.
Causation is unproven. The original 119-file source manifest still matched after
exit. No crashed-run fixture teardown or cleanup is assumed.

The four successful field requests took 9.656/9.656/9.657/9.828s, all status 200
under the explicit 60000ms correctness budget. Their integer input watermarks were
202/202/202/230: the authorized query's ledger input boundary, with the final
request after support withdrawal and worker recomputation. These are isolated
run-local commit sequence values, not global deployment progress or latency
acceptance. The preserved historical proof assertion passed afterward.

An additive concurrent-install/replay test ran separately after the native exit:
`s06-concurrent-final`, 1 passed, 15 deselected in 53.84s. Both initial calls returned
one queued job, and both completed replays returned the same succeeded JobRef
`db5fc1ec-b0ea-4411-9ad0-4d037d5cc407`; the actual installation count was 1.
Only that test was appended; production source and the earlier15 cases remained
unchanged. The controller authorized a diagnostic-only continuation after separate no-DB
checks. The original native crash remains causally unresolved. A new ignored
runtime harness (`.runtime/s06_safe_gate.py` with `s06_gil_dump.py`) replaces only
the optional native timed stack dump with a synchronous dump from a normal Python
thread after 180s. It stops/joins that thread before closing its integer descriptor,
retains fatal reporting and uses an external 3600s gate deadline. Deadline cleanup
requires saved actual pytest PID/creation/executable/parent identity and terminates
only that process and its launcher; descendant cleanup is not inferred. Product,
interpreter, dependency, query budget and assertions remain unchanged.

The actual-pytest no-DB positive smoke passed in 0.39s with one real dump, one joined
thread, no live diagnostic thread after join and fatal reporting enabled. A
separate 4s-deadline negative smoke returned 124, with verified exact-process
termination and complete sanitized output. These validate harness mechanics, not
resolution of the original native crash. The remaining six cases and existing 189
regression cases are run sequentially under this reviewed harness. Coverage must
be read across distinct commands/source hashes, never as one full 16-case run.

The six-case continuation ended **1 failed, 5 passed, 10 deselected in 1159.41s**.
Pond, orchard, device-family/inheritance rejection, failed-projection recovery and
component RDF reconstruction passed. The shared-device extension reached the
normal S02 submission gate and failed with `MIGRATION_PLAN_REQUIRED`: its fixture
had omitted the review-covered plan required for the new optional field's shape
change. A test-only correction supplies description, real SHACL validation and
rollback content before a fresh normal preview and independent approval. No
production gate or assertion was relaxed. The first focused rerun failed after
submission because the reviewer lacked current view rights to existing scene data:
1 failed, 15 deselected in 385.67s. F06 correctly hid the approval through its
ALL-target rule. A further fixture-only correction grants the independent reviewer
`viewer(scope=farm_a)` before fresh draft creation/preview, following the accepted
S02 current-data review contract. It changes no production authorization and does
not alter the unauthorized actor. The final focused outcome is recorded below.

| Context | Request durations (seconds) | Input watermarks |
|---|---|---|
| Field, original frozen run | 9.656 / 9.656 / 9.657 / 9.828 | 202 / 202 / 202 / 230 |
| Pond, continuation | 9.812 / 9.500 / 9.765 / 9.703 | 202 / 202 / 202 / 230 |
| Orchard, continuation | 9.641 / 9.578 / 9.578 / 9.734 | 202 / 202 / 202 / 230 |
| Shared case before its missing-plan rejection | 9.906 / 9.782 / 15.265 / 15.125 | 202 / 202 / 311 / 311 |
| Shared case before reviewer visibility correction | 9.578 / 9.360 / 15.312 / 15.297 | 202 / 202 / 311 / 311 |
| Shared case, final focused PASS | 9.843 / 9.813 / 15.219 / 15.156 | 202 / 202 / 311 / 311 |
| Component reconstruction | 9.765 / 9.781 / 9.656 | 202 / 202 / 202 |

All listed queries returned 200 under the explicit 60000ms budget. The shared
case has two loaded scenes: 10 distinct fixture object UUIDs plus two generated
farms imply 12 created scene objects before its queries. This count follows the
executed fixture flow; it is not a measured authorized-dataset selection count.
The two-scene durations strengthen the retained default-budget concern for D04.
The reconstruction gate verified exact physical graph identity/content, public
query/current proof and unchanged F03 historical proof after restoring saved RDF.

## Original S06 source-scoped results

This section records the original implementation committed as
`10e3e0193ef41e6482f3f43b31f0c37722114907`. The later fix-round evidence below
does not rerun this entire source-specific regression union.

`s06-shared-review-final` completed **1 passed, 15 deselected in 443.64s**, exit 0.
The actual fresh preview, independent approval, publication, generated form, new
subclass instance and shared-device identity/manager checks passed. Its reviewed
harness recorded one diagnostic dump, one joined thread, zero live diagnostic
threads, fatal reporting enabled and complete output without reader errors.

All **16 distinct kernel cases** have passing evidence across separate runs:
the original frozen run's eight installer cases plus field (9 checkpoints before
the native AV), the additive concurrent case (1 terminal pass), the continuation
(5 passes alongside the missing-plan fixture failure), and the final corrected
shared case (1 terminal pass). This is not a single full16-case successful run.
The production source is identical across these runs. The only later test deltas
are the appended concurrent case and the shared fixture's review-covered migration
plan and explicit reviewer view grant. Controller byte reconstruction verified
all earlier case bodies against their original frozen sources.

The required existing regression first ran all11 files below under the fixed
3600s external gate deadline. `s06-required-regression-final` exited124 at
2026-10-21T00:31:24.084833Z after3600.178s, with174 PASS,0 FAIL,0 ERROR and no native
fatal exception. The last entered case was
`test_lost_derived_remote_ack_keeps_manifest_and_recovers_same_output`; it was
interrupted, not failed. All174 preceding PASS reports had a subsequent test-start
boundary, demonstrating completed protocols. The180s synchronous diagnostic
emitted during the independently approved deactivation/budget case, which then
passed. It was not a business timeout. Exact owned pytest40860 and launcher53052
were identity-verified and terminated; descendant absence and interrupted fixture
teardown are not claimed. Output was complete, with no reader errors. The original
log and exit124 metadata are preserved, separate from the earlier aborted run.

The controller authorized only the15 unfinished cases:7 inference (including the
interrupted case),4 authorized queries and4 exports. The saved203-item collection
was filtered to the current11 unchanged test files, producing exactly189 nodeids;
all11 file hashes matched old/final/current snapshots, and the actual execution
order matched the first175 inventory entries. No new collection execution was
needed. Exact remaining nodeids and hashes are saved in
`.runtime/s06-required-remaining.json`.

`s06-required-remaining-final` completed **15 passed, 10 warnings in1177.32s**,
exit0, ending2026-10-21T00:52:23.894299Z. The warnings are upstream RDFLib JSON-LD
`default_context`/`ConjunctiveGraph` deprecations. Its actual pytest8160 and
launcher19116 completed normally:0 dumps,15 joined diagnostic threads,0 alive
after join, fatal reporting enabled, complete output, no reader errors, no cleanup
request. The real >1MiB literal export completed in11.781s; this is a single
regression observation, not a capacity benchmark.

Thus the existing regression evidence is **174+15=189 distinct passing cases**,
not one terminal189-case pass. Together with the16 new kernel cases, this covers
205 distinct selected cases through the explicitly attributed evidence union.
There were no skipped cases used as acceptance substitutes.

Exact native commands from the worktree (the harness loads the existing native
settings without exposing them):

```powershell
$env:S06_DIAGNOSTIC_SECONDS = '180'
$env:S06_GATE_DEADLINE_SECONDS = '3600'
.venv/Scripts/python.exe .runtime/s06_safe_gate.py s06-kernel-remaining-final apps/api/tests/integration/test_semantic_kernel.py -k 'pond or orchard or shared_identity or seven or failed_publication or component_rdf' -s --tb=short
.venv/Scripts/python.exe .runtime/s06_safe_gate.py s06-shared-review-final apps/api/tests/integration/test_semantic_kernel.py -k shared_identity -s --tb=short
.venv/Scripts/python.exe .runtime/s06_safe_gate.py s06-required-regression-final apps/api/tests/unit/test_semantic_profile.py apps/api/tests/unit/test_inference_closure.py apps/api/tests/integration/test_semantic_adapters.py apps/api/tests/integration/test_projection.py apps/api/tests/integration/test_schema_releases.py apps/api/tests/integration/test_dynamic_objects.py apps/api/tests/integration/test_fact_supports.py apps/api/tests/integration/test_geometry.py apps/api/tests/integration/test_inference_retraction.py apps/api/tests/integration/test_authorized_queries.py apps/api/tests/integration/test_semantic_export.py -s --tb=short
$s06Remaining = @((Get-Content -LiteralPath '.runtime/s06-required-remaining.json' -Raw -Encoding UTF8 | ConvertFrom-Json).remaining_nodeids)
.venv/Scripts/python.exe .runtime/s06_safe_gate.py s06-required-remaining-final @s06Remaining -s --tb=short
```

These commands were sequential. The earlier fixture failure and its intermediate
focused failure remain separately recorded; the commands are not presented as a
single all-final-source run. The original full kernel and additive command are
recorded above. Application/query/child budgets were unchanged by the diagnostic
harness; only optional pytest native timed dumping was disabled.

Each source manifest below covers119 Python/package/fixture files, excluding
Markdown and ignored runtime evidence. All119 latest hashes matched after the
terminal continuation. The package logical digest is
`dc248f26d1a14355035f902008ff4d3a4c4016f826ca5610adf29556668e1df4`.

| Manifest in `.runtime/` | SHA256 | Scope |
|---|---|---|
| s06-final-source.json | 4df3c739848cb99cd01477a7f0687bbb474fd944e9d5966fc7abf0404fe44f5d | Original15-case kernel source |
| s06-final-additive-source.json | 20cb58b68ec55a9b6977d55919f0ed13afa56242eab118118be55e5314d4093e | Appended concurrent case; remaining6 gate |
| s06-final-fixture-source.json | 48dc42fbe53eba948f73fadae91456e12c83f7485f8226be550cf0e9f3896c35 | Shared migration-plan correction |
| s06-final-reviewer-source.json | 270fa13cd91bc73eb11b1e258ea87f656302a4da895f21c29b5babbfec321aec | Reviewer grant; final shared and189 regression union |

The unchanged bootstrap SHA256 is
`489a36e60eff5bf2dcb227b0d263ba2e10dff154145fa956a46ac1502a7f6eed`;
latest kernel test SHA256 is
`d812b5e91b00439da99e1d9741522c8f827bc0eaf64761209c15fba441f11f23`.
These are physical workspace bytes; repository text normalization is LF.
The reviewed ignored harnesses remain named evidence risks for independent review:
`s06_safe_gate.py` SHA256
`c9763cd0ac302ddec5f63ae3c064d040c5ab06c5dda906d8d379badab941c069`,
and `s06_gil_dump.py` SHA256
`b3a3c87efc2db2409e899143483763693e29239cc199311538ec63b6c8337fb7`.
Their hashes and the smoke fixture hash were unchanged after final verification.
The continuation does not establish a fix for the original native access violation.

## Fix round1: reservation-wide installation identity

The bootstrap adapter now resolves actual installation identity across every
trusted preparation/repreparation draft in the namespace/version reservation.
It authorizes all actual JobRefs through current ALL dependency checks before
reporting ambiguity. A single compatible installation returns its original actual
JobRef regardless of the supplied approval, latest prepared draft or old pinned
revision. Its release document/import digests must match that historical draft's
immutable preparation, not editable metadata. Changed historical content raises
`BOOTSTRAP_INSTALLATION_CHANGED`; multiple actual installations raise
`BOOTSTRAP_INSTALLATION_CONFLICT`. Explicit reprepare shares this guard.

Original registered inputs, trusted RevisionRef and document/import digests still
undergo verification. Existing-job replay allows the existing single trusted
publication advance; strict equality between current head and prepared revision
applies before a genuinely new publication. Returning J neither retries/reset J
nor rebinds/re-enables its original approval A. Fresh independent approval B can
be executable while A is superseded; readback preserves J's actual queued/failed
state. Repair of an invalidated original approval remains separate governed work.
Ordinary S02/F06 publication, approval and worker behavior are unchanged.

The genuine RED corrects the review's initial duplicate-job premise: same-draft
queued/failed invocation with valid B hit the existing `(draft_id,draft_revision)`
unique constraint and escaped as IntegrityError, without persisting a second job.
The unchanged superseded-draft scenario did create a different actual JobRef.
Both paths now preserve reservation-wide identity. JSON index validation also
checks for a dictionary before its exact keys: actual registered null, number and
object-array roots now produce `BOOTSTRAP_INVALID` without semantic side effects.

Against unchanged FIX_BASE bootstrap, `s06-fix1-red` completed **8 failed,
1 passed,16 deselected in531.94s**, exit1. The passing revoked-dependency case
preserved existing authorization. The failed-worker case used a real one-attempt
worker against an unavailable local endpoint and verified persisted failed state;
it was not a mocked functional RED. Fresh B validity was verified through real
independent S02 review and F06 executable checks. Historical and ambiguous
installations came from ordinary reviewed S02 publication, not fabricated rows.

With only the adapter and focused test additions changed, `s06-fix1-green`
completed **15 passed,10 deselected in765.48s**, exit0, ending
2026-10-21T01:24:27.954590Z. This comprises9 new cases and6 affected existing
bootstrap cases. Queued/failed replays each retained one installation, the same J,
unchanged selected persisted installation/release/job fields and respectively
zero/one worker attempts.
Unchanged historical replay retained its original J; changed/ambiguous history
and explicit reprepare refused without altering existing installation state.
Current dependency revocation and all3 malformed-root cases passed.

```powershell
$env:S06_DIAGNOSTIC_SECONDS = '180'
$env:S06_GATE_DEADLINE_SECONDS = '3600'
.venv/Scripts/python.exe .runtime/s06_safe_gate.py s06-fix1-red apps/api/tests/integration/test_semantic_kernel.py -k 'reservation_replays or reservation_checks or reservation_replay_rechecks or registered_index' -s --tb=short
.venv/Scripts/python.exe .runtime/s06_safe_gate.py s06-fix1-green apps/api/tests/integration/test_semantic_kernel.py -k 'reservation_replays or reservation_checks or reservation_replay_rechecks or registered_index or requires_normal_approval or explicit_reprepare or concurrent_install or changed_input_bytes or failed_publication or rejects_missing_extra' -s --tb=short
```

RED ran01:01:41.163620Z–01:10:34.325155Z; GREEN ran01:11:41.250238Z–01:24:27.954590Z
on2026-10-21. Actual pytest PIDs were62168 and19700; owned launchers63444 and25768.
Reviewed harness hashes above remained unchanged. Both gates had complete output,
no reader errors, no native fatal exception, no cleanup request and fatal reporting
enabled. RED/GREEN diagnostic threads joined9/15 times, with0 dumps and0 left
alive. The180s diagnostic and3600s deadline did not alter application budgets.

Frozen `.runtime/s06-fix1-green-source.json` contains119 hashes, SHA256
`853d463d4dce2109ed57ba2305c5d93a3bd6f41e75d58fdc556b8b764c9ddcca`.
Only bootstrap and kernel test differ from the original119-file source manifest.
Bootstrap SHA256 is
`533eb6465e9063c1125ae16232238d822b08bdcad41d33a43ffbfc77f72681b6`;
test SHA256 is
`b121709f3919035ca357f1f892619c16ac3cd7a1fa8a2b33b10c9308cdd7b0e1`, identical
in RED/GREEN. These are physical workspace bytes; repository normalization is LF.

The earlier205 distinct passing cases remain attributed to original S06 sources.
They were not rerun on this fix; only the15 affected cases above ran on the fix
source. No full scene,189-case prefix, native diagnostic probe or dependency
upgrade was repeated. The10 upstream RDFLib warnings remain recorded and accepted;
no warning suppression or dependency change was made. Original AV causality,
60000ms scene correctness budget/default-budget usability, component-only RDF
reconstruction and all unexecuted acceptance boundaries remain unchanged.

## Unexecuted acceptance gates

Docker and WSL are unavailable. The prescribed `docker compose -f
infra/compose/compose.yaml --profile test run --rm api-test pytest ...` commands
are unexecuted. Native evidence does not imply Compose, Linux, offline,
cross-store restore, browser, connector, load or benchmark acceptance. The
workstation is not the standard eight-core benchmark environment. The earlier
unexplained Windows access violation from S05 remains a separate recorded limit.
