# Independent approvals

F06 supplies immutable approval snapshots and one independent decision per
snapshot. It does not implement Schema modeling, publication, recalculation,
or field-conflict adjudication. Those producers register trusted policies and
call the gate in their own transaction.

`0005_approvals` follows `0004_jobs`. The current head is
`0005a_approval_footprint_seed`: the additive correction marks all pre-existing
users as having unknown prior authorization history, including removed group
members. Neither migration changes an applied predecessor. Runtime credentials
have SELECT/INSERT, and no UPDATE/DELETE, on snapshots, decisions, dependencies,
and authorization footprints.

## Producer contract

`ApprovalService(uow)` preserves these signatures and never commits:

```python
submit(actor, kind, target_ids, change_digest, baseline, impact) -> ApprovalRef
decide(actor, approval_id, decision, comment) -> ApprovalRef
require_executable(actor, approval_id, change_digest, baseline) -> ApprovalRef
```

The typed `ApprovalRef` is in `governance.approval_schemas`. `impact` at the
submit boundary is exactly `{"command": validated_command}`; callers cannot
supply risk flags or an authoritative impact report. `preview(actor, kind,
target_ids, command)` resolves the canonical descriptor, digest, baselines,
and server-derived impact.

Register a trusted resolver with `governance.risk.register_policy(kind,
resolver)`. The callable receives `(uow, target_ids, command)` and returns a
`ChangeDescriptor`. Registration is deployment code, never an HTTP module
name or executable payload. Future S02/I05 modules can register alongside
their worker handlers using the existing trusted module-loading mechanism.
Unknown kinds, unresolved/unknown baseline keys, incomplete descriptors,
and changed content fail closed.

Each resolver must:

- Parse a strictly bounded, typed command and derive canonical content from
  the actual domain operation; never echo caller risk, impact, or baselines.
- Enumerate every protected target, impact object, input, and evidence
  reference in `target_ids`. These dependencies control approval and history
  visibility before pagination.
- Read every required revision from authoritative storage in the supplied
  UoW. Baseline keys identify resources, rules, or integer input watermarks;
  all values are nonnegative revision numbers.
- Declare separate submit, review, and execution actions. Schema producers
  should use `schema.edit`, `schema.review`, and `schema.publish` as appropriate;
  review does not imply edit and edit does not imply publish.
- Supply a stable `operation_key` identifying the target operation, and a
  `policy_version`. The latest submission by the same submitter for that key
  supersedes its predecessor. Keys must distinguish unrelated operations.
- Set `unresolved_conflicts` from the real conflict store. Approval cannot
  adjudicate new field conflicts or bypass a separate ruling.

Snapshots contain the exact canonical command, content digest, full baseline,
impact, target references, submitter, timestamp, policy version, system floor
version, and explanatory risk reasons. Decisions retain reviewer, comment,
decision, and time. Changing the resolver's version or resolved descriptor
invalidates an old approval. The system floor permits low risk only for
trusted nonsemantic label/description-only changes and ordinary authorization
management. Semantic schema changes, risk-rule changes, recalculation and
effective privilege expansion remain high risk.

`require_executable` re-resolves the stored command and real baseline, compares
the execution caller's freshly computed digest/baseline, checks the original
submitter's current submit and execute rights, and checks the independent
reviewer's current review rights. Unrelated authorization-version changes do
not invalidate an otherwise authorized actor: workers use fresh ActorContext
versions. The same stable initiating user executes; there is no admin fallback.

The gate is a transactional precondition, not a separate consumption commit.
The domain command must advance the bound target revision (or its own durable
consumption/input revision) in the same UoW as its effect. An approval does
not authorize repeated effects against an unchanged version. Use producer
idempotency for replay. In workers, call the gate inside `ctx.batch()` before
domain effects and checkpoint, and let the batch own commit.

Lock ordering is authorization state → ledger → resource/job locks. Identity
mutations acquire the authorization state exclusively before checking any
approval. Ordinary preview/review/execution takes its shared lock first.
Do not acquire a shared authorization lock then upgrade it for an identity
mutation in the same transaction.

## HTTP

The typed endpoints are `POST /api/v1/approvals/preview`, `GET/POST
/api/v1/approvals`, `GET /api/v1/approvals/{id}`, and `POST
/api/v1/approvals/{id}/decisions`. Preview is read-only; business submission and
decisions require `Idempotency-Key`, and a decision additionally requires a
quoted `If-Match` revision. All cookie-session POSTs require CSRF. Current rights
are checked before idempotency replay. Visible approvals expose canonical
command and impact for review. Losing any referenced target's visibility hides
the approval and its dependency-protected ledger events.

## Authorization administration

`auth.approval_policy` covers `identity.grant.create`, `identity.role.update`,
and `identity.group.members`. It compares complete effective rights across
direct grants and group inheritance; targets distinguish object, scope, and
global bindings. Genuinely initial scoped basic view/edit/use assignment may
be low. Further privilege increases, cross-scope/global access, and important
actions require independent approval. Pure revocations remain immediate.

Append-only per-user footprints mark prior governance without conferring
rights. They prevent revoke/regrant or remove/re-add sequences from resetting
initiality. Pre-F06 identities are conservatively noninitial; new users created
after migration can still receive an initial ordinary assignment. The adapter
binds relevant user/role/group/grant revisions and affected-user footprint
counts, not the global authorization version.

Role, grant, and membership writes accept optional `approval_id`. Preview a
grant with the GrantWrite command excluding `approval_id`; a role update uses
`{"role_id": "...", "expected_revision": 1, "body": {"name": "...",
"actions": [...]}}`; membership uses `{"group_id": "...",
"expected_revision": 1, "user_ids": [...]}`. Pass the approved ID to the actual
write. The service boundary independently enforces the exact command binding.
Authorization audit events retain the approval reference and its visibility
dependencies.

Owner-operated bootstrap remains separate tooling. There is no normal API
installation flag. Initial administrators and synthetic acceptance identities
must be provisioned through that controlled installation path.

## Acceptance

`tests/integration/test_approval_gates.py` exercises real PostgreSQL snapshots,
decisions, upgrades, current authority, immutable storage, HTTP preconditions,
visibility, and effective authorization expansion paths.

`tests/e2e/approval-gates.spec.ts` uses real Keycloak authorization-code login
for two synthetic accounts, the actual API and worker, and test-only domain
policies in `tests/e2e/support/approval_domain.py`. The submitter explicitly has
review authority yet cannot self-approve; the other reviewer has no edit/publish
rights. A positive publication commits one synthetic effect and advances its
revision. A second independently approved job, after reviewer revocation,
commits zero effects. Test-only policy loading must be removed from native
API/worker configuration after acceptance. No production test route exists.

For native Windows acceptance, normalize both color environment variables
before launching Playwright so its worker does not inherit conflicting flags:

```powershell
Remove-Item Env:NO_COLOR -ErrorAction SilentlyContinue
Remove-Item Env:FORCE_COLOR -ErrorAction SilentlyContinue
$env:LZO_BROWSER_EXECUTABLE = 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'
.tools/node/node-v24.22.0-win-x64/node.exe node_modules/@playwright/test/cli.js test tests/e2e/approval-gates.spec.ts --reporter=list
```

Native evidence does not satisfy the still-required Compose and offline
deployment gates, which need a capable host.

## Trusted operation targets

`ChangeDescriptor.operation_target_ids` is an optional server-producer field.
Omitting it preserves the original ALL-target phase-action requirement and the
old canonical digest/snapshot format. Existing stored records are not rewritten.
An explicit tuple must contain unique UUIDs, be nonempty, be a subset of
`target_ids`, and include its primary first target. The explicit subset is part
of both the digest and persisted descriptor, so changing it invalidates an
earlier preview or approval.

All protected targets still require current `view` authorization before any
phase operation. For scoped producers only, submit/review/execute actions apply
to the explicit operation targets; read-only source and Schema evidence is not
silently granted edit rights. Existing `global_security_class` behavior remains
unchanged. This is a trusted producer interface, never an HTTP-selected flag.
S03 uses it for `object.deactivate`: `edit` submit/execute, independent
`object.review`, complete source/Schema/inbound dependencies, and a high-risk
approval. Current or future accepted incoming links from other active objects
block retirement; hidden impact returns a generic 404, visible blocking impact
returns `OBJECT_HAS_INBOUND_RELATIONS` (409).
