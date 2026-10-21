# Identity and authorization operations

The API is an OIDC BFF. Browser cookies are opaque, Secure, HttpOnly, SameSite=Lax, and host-only. Provider tokens and temporary PKCE/nonce values are encrypted in PostgreSQL with `LZO_SESSION_ENCRYPTION_KEY` (a Fernet key). Keep this key in the deployment secret store and back it up with database recovery material. Rotating it requires users to log in again. Ordinary passwords are handled only by Keycloak.

## Provisioning

Run migrations with the owner-only migration environment. The API and worker must not receive owner or bootstrap-administrator credentials.

Use `scripts/configure_identity.py --env <operator-env> [--smtp-json <file>]` from the project virtual environment to configure the actual realm through the Keycloak admin API. Realm imports do not update an existing realm. The operator environment requires the bootstrap administrator credential, public origin, client secrets, and optional local CA path. The script creates/updates the confidential S256 login client and a separate client-credentials identity adapter. The adapter receives only Keycloak `realm-management` roles `manage-users` and `query-users`; it does not receive `realm-admin` or client administration permissions.

The SMTP JSON is a Keycloak `smtpServer` configuration object for the operator's intranet mail server. Never enable `LZO_INVITATION_SMTP_CONFIGURED` without configuring and verifying that server. Without SMTP, invitations fail explicitly. Native synthetic acceptance uses a real loopback SMTP capture endpoint accepting only `@example.invalid`; it is not a production mail service.

Configure `LZO_PUBLIC_ORIGIN` and `LZO_OIDC_ISSUER` exactly for the selected profile. Native uses `https://localhost:18443`; Compose defaults to `https://localhost:8443`. `LZO_OIDC_BACKCHANNEL_BASE` optionally routes server calls to the internal Keycloak address while validating the public issuer. It never changes the browser authorization URL or accepted issuer. Configure `LZO_IDENTITY_ADMIN_URL` for the same realm. Restart the API after changing its environment.

Bootstrap the first platform administrator explicitly with `PYTHONPATH=backend/src` and:

```text
python scripts/bootstrap_authorization.py --env <operator-env> --issuer <exact-issuer> --subject <verified-Keycloak-subject>
```

This creates a platform `identity.manage`/`view` role and a global grant limited to security class `identity`, with ledger records. Email addresses and Keycloak realm roles never confer platform administration.

## Durable user operations

`POST /api/v1/users/invitations` and `PATCH /api/v1/users/{id}` return an `IdentityOperationDetail` on confirmed completion. Its `user` contains the typed user detail. An incomplete provider operation returns the shared typed API error, not a delivery-success claim. Administrators can inspect paginated `GET /api/v1/identity-operations` and `GET /api/v1/identity-operations/{id}`.

Each operation first commits an actor-scoped idempotency result, intent revision, audit event and outbox event. Disabling a user also commits local denial, authorization-version advancement, and deletion of all BFF sessions before any provider call. A failed or interrupted provider disable therefore cannot retain an active platform session.

Invitation creation uses an operation-derived Keycloak username. Recovery searches that exact username and never merges accounts by email. The provider subject is committed before sending the email. A `sending` intent interrupted before local completion becomes `delivery_unknown` on recovery; the application cannot infer whether SMTP accepted or delivered the message and never automatically resends it. `completed` means Keycloak confirmed its email-send operation, not that a person read it. Activation and email verification remain Keycloak required actions.

Operator recovery uses current platform permissions:

```text
python scripts/reconcile_identity.py --env <operator-env> --actor-id <platform-admin-uuid> --operation-id <operation-uuid>
```

Recovery repeats safe provider enable/disable operations and resolves provider creation by operation identity. A newer user revision supersedes an older pending enable/disable. It does not resend ambiguous invitations or label ambiguous delivery as successful. Inspect provider/SMTP records to resolve such cases; no automatic exactly-once email delivery is claimed. The ledger retains every recorded phase. F05 may later consume these existing intents through a worker.

Business mutations require CSRF and `Idempotency-Key`; modifications of existing resources also require quoted `If-Match`. Refresh and logout require CSRF but follow session protocol rather than business idempotency. Logout posts the server-held refresh token directly to Keycloak and removes the BFF session; no provider token is placed in a browser URL.

## Downstream service contracts

F06 gates effective privilege increases at the `AuthorizationAdmin` service
boundary for grants, used-role updates, and group membership changes.
These writes accept `approval_id`; see [independent approvals](approvals.md)
for the trusted preview command formats, current reviewer checks, and
append-only authorization footprints. Pure revocation remains immediate.

Use `auth.router.authenticated` as the FastAPI dependency. It yields `AuthRequest(uow, service, actor, row)` and binds `auth.permissions.permission_context(uow)` for the exact public `require_permission(actor, action, resource)` and `authorized_resource_ids(actor, action)` signatures. Services outside HTTP should explicitly bind that context or pass `uow=`. Writes share the same UoW as their permission check and must commit explicitly. Version checks hold a PostgreSQL share lock until transaction end; authorization mutations serialize on its exclusive lock. Cached actors with an outdated version are rejected.

`resource_scopes` is the generic registry in `auth.models`. Producers insert its `resource_id`, `resource_kind`, `security_class` and explicit authorization `scope_id` in the same UoW as resource creation. `owner_scope_id` and `related_scope_ids` are informational; they never grant access. Shared equipment should retain its original registry scope, with a complete object-target grant for a recipient's allowed operations. Do not add the equipment to another broad scope merely because it serves that area.

Roles contain explicit action names; grants bind one user or group, one role, one security class, and exactly one explicit scope/object/global target. Foundation uses `view`, `edit`, `use`, and `identity.manage` in its tests and bootstrap. Modules can define their own action vocabulary through role administration; there are no implicit wildcards or automatic data/schema rights. `view` controls current-right history. Lists/aggregations must compose `authorized_statement` or consume `authorized_resource_ids` before pagination/counting, never filter unauthorized records in the caller afterward. Page defaults and caps are operator settings `LZO_LIST_PAGE_SIZE` and `LZO_LIST_MAX_PAGE_SIZE`.

`authorized_statement(actor, action)` only constructs a SQL filter. Before executing it or a query containing it, call `validate_actor(uow, actor)` in the same UoW, with no intervening commit or rollback. This validates enabled state and the current authorization version and retains the shared version lock until transaction end. Authentication alone does not satisfy this prerequisite. `require_permission`, `authorized_resource_ids`, and `HistoryService` already perform that validation.

History APIs use `HistoryQuery`, `RevisionDetail` and `RevisionDiff` from `governance.history`. After `append_revision`, producers may insert the returned `event_id` into `governance.history.revision_metadata` within the same UoW, with actual `task_id`, `bundle_id`, and string UUID arrays for evidence/proof references. The metadata table is append-only for the runtime role. Do not put a request ID into `task_id`, fabricate provenance, or update an existing metadata record. History filters current permissions before resource/task/actor/time search and cursor pagination.

## Native acceptance

The reproducible native setup is:

1. Start the existing native services and run migrations without removing their data.
2. Start `tests/e2e/support/smtp_capture.py --directory .runtime/f03-mail` as a hidden local process.
3. Configure SMTP to loopback port `11025` through `configure_identity.py`.
4. Run `tests/e2e/support/provision.py` to create only synthetic browser accounts directly in Keycloak and bootstrap the synthetic administrator.
5. Run the API test suite and `tests/e2e/oidc-session.spec.ts`, setting `LZO_BROWSER_EXECUTABLE` to the verified installed Edge executable on Windows.

OIDC browser tracing/screenshots/video are disabled to avoid retaining credentials, action URLs, or session data. API access logging omits URLs; nginx logs `$uri` without query parameters. Test evidence must remain sanitized. Container replay is a separate acceptance gate; native results do not claim it passed.
