# 本体农场工作台

当前目录是 React 工作台和 Studio 的开发原型，包含真实 OIDC 会话、Schema 编辑、导入、审批与发布界面及已有单元测试。前端仍需按用户参考重新设计，现有视觉方案不是最终批准版本。产品开发暂停，本次快照不新增界面或功能；验收边界见[快照状态](../docs/status/2026-10-21-source-snapshot.md)。核心语义与权限决策由 `backend/` 执行。

Use the repository's pinned Node 24.22.0. The web application has its own
lockfile and dependency tree; root npm remains the Playwright harness.

```text
npm ci --prefix frontend
python scripts/export_openapi.py
npm run api:generate
npm run web:test
npm --prefix frontend run typecheck
npm run api:check
npm run web:build
```

The exporter imports the actual API without connecting to services. Generated
TypeScript reads only the local OpenAPI snapshot. `api:check` regenerates into
a temporary file and compares bytes. Use `python scripts/export_openapi.py
--check` to additionally compare the snapshot against current backend source.

`/overview`, `/ontology/studio` and `/governance` have implemented views;
other named routes render explicit pending pages. All fonts and SVG illustrations are local. The static app, `/api/v1`,
OIDC callbacks and `/identity` are served through the HTTPS proxy. Build the
proxy with `frontend/Dockerfile`; its `test` target runs the web checks.

Session snapshots and CSRF remain in memory. Protected query keys begin with
stable user ID and authorization version. Hidden tabs conceal content; entry,
focus/visibility return and a visible 30-second poll reconcile the real BFF
session. `VITE_SESSION_POLL_MS` changes the polling interval at build time.
BroadcastChannel carries only lifecycle signals. The backend reauthorizes
every request. Refresh does not extend the fixed BFF expiry. Mutations never
automatically replay; `CommandIntent` retains an exact same-user request/key
for an explicit retry and disposes it on invalidation.

## Real browser fixtures

Provision an isolated test database and realm first. Set `APP_ENV=test`,
`LZO_TEST_PROJECT=lzo-test-<owned-name>`, `LZO_TEST_DATABASE=<exact-db>`, and
the normal BFF settings in a private fixture env file. Supply exact real
provider subjects for `editor-a` and `editor-b` in a separate JSON file.
Run `scripts/seed_workbench.py --env <file> --subjects <file> --output <manifest>`.
This auth-only seed uses audited identity registration/bootstrap owners;
it does not provision provider passwords or seed business data.

The browser runner uses `LZO_BASE_URL`, `LZO_E2E_PASSWORD`, and optionally
`LZO_BROWSER_EXECUTABLE`. The test control process additionally needs
`LZO_FIXTURE_PYTHON`, `LZO_FIXTURE_CONTROL=tests/e2e/support/workbench_fixture.py`,
`LZO_WORKBENCH_ENV` and `LZO_WORKBENCH_MANIFEST`. Its restricted provider
service account needs only manage-users/query-users in the owned test realm.
Fixture controls manipulate only manifest-bound test sessions and audited
authorization; they are never exposed through production HTTP routes.

Run `npm run test:workbench`. Credential-bearing tests disable traces, videos
and failure screenshots, and blank pages before automatic error snapshots.
Screenshots are explicitly taken only after login on the nonsecret shell.

For Compose acceptance, use an isolated `lzo-test-*` Compose project and set
`LZO_PUBLIC_ORIGIN=https://proxy:8443` consistently for provider issuer,
redirect registration, API CSRF and the test browser. Mount the seed manifest
and private fixture env under `infra/compose/workbench-fixtures` (ignored and
excluded from image context). The supplemental native fixture can use a
separate real provider origin; that does not establish Compose acceptance.
