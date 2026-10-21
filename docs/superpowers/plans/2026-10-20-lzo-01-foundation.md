# 平台基础与治理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立真实持久化、OIDC、绑定范围授权、不可变修订、持久任务及风险审批基础。

**Architecture:** API 与 Worker 共享 PostgreSQL 事务服务；通过稳定用户身份、授权版本、乐观锁和 outbox 保持跨模块行为一致。身份认证交给 Keycloak，平台自行治理授权、裁决与发布。

**Tech Stack:** Python 3.12、FastAPI、Pydantic v2、SQLAlchemy 2/Alembic、psycopg、PostgreSQL/PostGIS、Keycloak、Authlib、Docker Compose。

**Spec:** [已确认设计](../specs/2026-10-20-lzo-platform-p0-p1-design.md) §5–6、§10、§12、§14；同时阅读[总计划](2026-10-20-lzo-p0-p1-implementation.md)的接口与执行约定。

## Global Constraints

- 高风险提交人与审核人不同；历史不可原位改写；原始、候选、采纳、派生事实分开。
- 核心功能断开外网仍正常运行，所有关键静态资源和依赖能够内网部署。
- 用户/组、角色/操作、区域必须绑定同一授权项；共享服务关系不自动授予权限。
- 每项任务还适用总计划全部 Global Constraints。首版真实设备控制保持关闭。

## Review Focus

沿用总计划五项，其中 F03 负责多角色范围与历史权限负例，F06 负责批准后数据/权限变化，F05 负责租约过期和重复交付。F01 还验证重建容器后数据存在；F02 验证并发、幂等冲突及历史不可改写。

## 文件分工与测试基础

`lzo/db.py` 管事务；`governance/revisions.py` 管修订；`auth/` 管会话和授权；`artifacts/` 管不可变二进制；`worker/` 管任务；`governance/approvals.py` 管批准。不得将其集中到 `main.py`。

测试 fixture 由 `apps/api/tests/conftest.py` 分任务增加：F01 `db_session/api_client/compose_endpoints`；F02 `uow`；F03 `actor/auditor/other_actor`（互不相同的稳定 UUID、明确范围）；F04 `artifacts`；F05 `jobs`；F06 `approvals`。场景 fixture 不代替真实数据库。

### Task F01：可重现的工具链与真实服务启动

**Files**
- Create: `.gitignore`、`.gitattributes`、`README.md`、`infra/versions.lock.json`、`infra/compose/compose.yaml`、`infra/compose/.env.example`
- Create: `infra/fuseki/Dockerfile`、`infra/fuseki/config.ttl`、`infra/keycloak/realm-template.json`、`infra/proxy/nginx.conf`
- Create: `apps/api/pyproject.toml`、`apps/api/requirements.lock`、`apps/api/Dockerfile`、`apps/api/src/lzo/main.py`、`apps/api/src/lzo/config.py`、`apps/api/src/lzo/db.py`
- Create: `scripts/dev.ps1`、`scripts/dev.sh`、`scripts/check_environment.py`、`docs/operations/environment.md`
- Create: `package.json`、`package-lock.json`、`playwright.config.ts`、`infra/testing/Dockerfile.e2e`（仅测试运行器；产品 Web 脚手架由 U01 创建）
- Test: `apps/api/tests/conftest.py`、`apps/api/tests/integration/test_bootstrap.py`

**Interfaces**
- Consumes: 本地只读环境核查及确认的组件职责，无既有代码。
- Produces: `create_app() -> FastAPI`、`Settings`（数据库、Fuseki、OIDC、文件根路径从环境读取）；`GET /health/live`、`GET /health/ready`。
- Produces: Compose 服务 `api/worker/postgres/fuseki/keycloak/proxy`、测试 profile `api-test/web-test/e2e-test`，来源 profile `source-postgres/source-mysql`；数据服务仅内部网络可达。
- Produces: 根目录 Playwright 测试 harness，F03 可直接测试 API 登录重定向；U01 后续扩展 Web 构建与浏览器工作台测试，不让身份测试依赖尚未创建的前端。

- [ ] **Step 1：准备运行条件并写启动/持久化验收测试。**
  执行 `check_environment.py` 的预检设计：实际检查 Git、Node、Compose daemon、WSL 发行版与容器可用性，记录硬件、磁盘类型与可用空间；没有即准备受控工具目录或受支持容器运行时，不修改全局 Anaconda。执行时使用 using-git-worktrees 指定隔离工作区；无仓库时先保护原文、初始化仓库并记录文档基线，不依赖远程 Git。锁定 Node 24 LTS 系列与 Python 3.12 的确切补丁，再解析支持的 PG/PostGIS、Keycloak、Jena 组合；锁定精确版本、镜像摘要和许可证，不复制旧文档示例版本号。
  `test_bootstrap.py` 断言：
  ```python
  def test_real_services_persist_across_restart(compose_endpoints):
      probe = compose_endpoints.write_unique_pg_and_rdf_probe()
      compose_endpoints.restart_without_volume_deletion()
      assert compose_endpoints.read_pg_probe(probe.id) == probe.pg_value
      assert compose_endpoints.ask_rdf_probe(probe.iri) is True
      assert compose_endpoints.oidc_discovery()["issuer"] == compose_endpoints.issuer
  ```
  `compose_endpoints` fixture 封装实际 HTTP/SQL 与 Compose 重启，仅允许专用测试 project 的命名卷；禁止 `down -v` 针对用户数据。
- [ ] **Step 2：运行失败检查。**
  `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_bootstrap.py -q`；预期因尚无服务/配置而失败。工具未就绪时记录明确环境阻塞，不能把该失败写成应用缺陷或测试已执行。
- [ ] **Step 3：实现最小真实启动链。**
  创建 API、健康端点、依赖锁和测试镜像；Jena 从固定官方发行包构建，TDB2 使用持久卷；Keycloak 使用自己的 PG database/user，不把其内部表混入平台 schema。主机同源代理将平台和本地身份服务暴露为可配置 HTTPS 域名，正确配置 issuer/回调；生成开发证书说明。平台健康不依赖可选外部来源。`worker` 本任务只提供运行入口，具体任务循环在 F05。只在计划交付的脚本中打印版本和状态，不打印密码。
- [ ] **Step 4：运行真实启动及重启检查。**
  前述 pytest 命令通过；`docker compose ... config --quiet` 成功；测试读回 PG 记录和 RDF 三元组并验证 Keycloak discovery。`versions.lock.json` 保存实际成功组合；尚未实现的 Semantica 能力在 S01 验证，不能宣称全部语义集成已验证。
- [ ] **Step 5：审阅并提交。**
  仅暂存上述明确文件；`git commit -m "build: establish reproducible persistent platform services"`。README 说明已启动能力、待实施能力及本机与标准验收机器的差异。

### Task F02：事务修订、乐观锁、幂等及 outbox

**Files**
- Create: `apps/api/alembic.ini`、`apps/api/alembic/env.py`、`apps/api/alembic/versions/0001_core_ledger.py`
- Create: `apps/api/src/lzo/contracts.py`、`apps/api/src/lzo/errors.py`、`apps/api/src/lzo/governance/models.py`、`apps/api/src/lzo/governance/revisions.py`、`apps/api/src/lzo/governance/idempotency.py`
- Create: `apps/api/src/lzo/governance/history_snapshot.py`（后续能力协议，不注册首版回放端点）
- Modify: `apps/api/src/lzo/db.py`、`apps/api/src/lzo/main.py`、`apps/api/tests/conftest.py`
- Test: `apps/api/tests/integration/test_revision_transactions.py`

**Interfaces**
- Consumes: F01 `Settings` 与 PG。
- Produces: 总计划全部公共 DTO、`UnitOfWork` 和 `append_revision(...)`。
- Produces: `ApiError(code: str, status: int, message: str, details: JsonObject | None)`；`Conflict(code: str, message: str, details: JsonObject | None = None)` 固定409，`Forbidden(...)` 固定403；响应 code 使用 UPPER_SNAKE_CASE，异常字符串含 code，供跨模块测试与 UI 错误映射统一消费。
- Produces: `execute_idempotent(uow, actor, key: str, body_digest: str, command: Callable[[], JsonObject]) -> JsonObject`。
- Stores: `resource_heads`、`revisions`、`audit_events`、`outbox_events`、`idempotency_results`；每条 revision 带前版本/前摘要、内容摘要、操作者、请求与有效/记录时间。平台全局 `event_position/input_watermark/projection_watermark/inference_watermark` 为整数提交序号；提交序号在事务内分配，未提交/已回滚事件不能令消费者跳过早先未完成事件。消费水位表示连续完整处理范围，不等于已处理事件的最大编号。
- Extension: `HistoricalStateRequest(resource_ids: tuple[UUID,...], recorded_at: datetime, effective_at: datetime | None)`、`HistorySnapshotRef(id: UUID, ledger_watermark: int, artifact: BlobRef)`；`HistoryReconstructionPort.reconstruct(actor: ActorContext, request: HistoricalStateRequest) -> HistorySnapshotRef` 仅定义未来适配器协议，首版不注册实现/公开按钮。修订链、业务/系统时间及依赖版本为其保留输入，D03备份这些数据。

- [ ] **Step 1：写事务与并发负例。**
  ```python
  def test_stale_edit_and_failed_outbox_do_not_half_commit(ledger_scenario):
      first = ledger_scenario.edit(expected_revision=0, value="初始")
      assert first.revision == 1
      assert ledger_scenario.edit_error(expected_revision=0) == "REVISION_CONFLICT"
      ledger_scenario.fail_between_revision_and_outbox(expected_revision=1)
      assert ledger_scenario.head_revision == 1
      assert ledger_scenario.outbox_count == 1
  ```
  在本测试文件定义 `ledger_scenario` 使用真实 PG 的两个 Session；另测相同幂等键同内容返回原结果、不同内容 409、两个并发写仅一个成功、普通应用 DB 角色 UPDATE/DELETE 历史被拒绝。并发阻塞/回滚一个写事务时，消费者不得因后续事务已完成就漏掉早先修订；水位测试同时覆盖此提交顺序。
- [ ] **Step 2：执行 `pytest tests/integration/test_revision_transactions.py -q`，确认导入缺失或行为失败。**
- [ ] **Step 3：实现事务边界和标准错误。**
  资源头使用条件更新/行锁；revision、audit、outbox、幂等结果同事务提交。历史表仅允许 INSERT，变更当前投影不修改历史；DB 迁移管理员与运行账户分离。`If-Match`/幂等中间件使用总计划状态码，记录错误与 request_id，敏感字段脱敏；迁移可在空库和已有库顺序执行。
- [ ] **Step 4：运行本任务测试和 F01 健康测试；所有并发、失败注入及历史禁止改写断言通过。**
- [ ] **Step 5：暂存本任务文件并提交 `feat: add immutable revision ledger and transactional outbox`。**

### Task F03：真实 OIDC 会话、区域授权与历史查询

**Files**
- Create: `apps/api/alembic/versions/0002_identity_authorization.py`
- Create: `apps/api/src/lzo/auth/{models,schemas,oidc,identity_admin,session,permissions,router}.py`
- Create: `apps/api/src/lzo/governance/{history,history_router}.py`
- Modify: `infra/keycloak/realm-template.json`、`apps/api/src/lzo/main.py`、`apps/api/tests/conftest.py`
- Test: `apps/api/tests/integration/test_scoped_permissions.py`、`apps/api/tests/integration/test_history_access.py`、`tests/e2e/oidc-session.spec.ts`

**Interfaces**
- Consumes: F02 `ActorContext/ResourceScope`、修订服务。
- Produces: `require_permission(...)`；`authorized_resource_ids(actor: ActorContext, action: str) -> Iterator[UUID]`，在数据库按完整授权项筛选并分批返回，不能先返回无权对象让调用者过滤。
- Produces: `HistoryQuery(resource_id: UUID | None, resource_kind: str | None, actor_id: UUID | None, task_id: UUID | None, recorded_from: datetime | None, recorded_to: datetime | None)`；`HistoryService.list(actor, query: HistoryQuery, cursor: str | None) -> Page[RevisionRef]`；`HistoryService.diff(actor, resource_id: UUID, from_revision: int, to_revision: int) -> RevisionDiff`。GET `/history` 接受对应可选参数，后端先按当前权限筛选再检索与分页，支持跨资源审计。
- Produces: `HistoryService.get(actor, resource_id: UUID, revision: int) -> RevisionDetail`；`RevisionDetail(ref: RevisionRef, payload: JsonObject, actor_id: UUID, task_id: UUID | None, recorded_at: datetime, effective_at: datetime | None, bundle_id: UUID | None, evidence_refs: list[UUID], proof_refs: list[UUID])`。
- DTO: `RevisionDiff(resource_id, from_revision, to_revision, added: JsonObject, removed: JsonObject, changed: JsonObject, actors: list[UUID], event_ids: list[UUID])`；Schema/几何语义差异由 S02/S03 适配扩展。
- HTTP: `/session/login`、`/session/callback`、`GET /session`、`POST /session/refresh`、`POST /session/logout`；`/users`、`/users/invitations`、`/groups`、`/groups/{id}/members`、`/roles`、`/grants`；`GET /history?resource_id=`、`GET /history/{id}/revisions/{revision}`、`GET /history/{id}/diff?from_revision=&to_revision=`。

- [ ] **Step 1：写范围授权、会话及历史负例。**
  ```python
  def test_roles_do_not_cross_product_and_history_uses_current_rights(scope_scenario):
      scope_scenario.grant("alice", "editor", "farm_a")
      scope_scenario.grant("alice", "viewer", "farm_b")
      assert scope_scenario.allowed("alice", "edit", "object_a")
      assert not scope_scenario.allowed("alice", "edit", "object_b")
      scope_scenario.revoke("alice", "farm_a")
      assert scope_scenario.history_status("alice", "object_a") == 404
  ```
  `scope_scenario` 明确创建3位用户、2区域、3对象（包含共享设备）、角色和完整授权项；另测仅服务关系不赋权、撤权旧会话无效、同邮箱不同 issuer/sub 不合并。浏览器直接访问 API 登录端点并完成真实 Keycloak 授权码/刷新/注销，用会话请求验证结果，不依赖 U01 页面；断言 localStorage 无 Token、缺失 CSRF 修改被拒绝、错误 issuer/audience/nonce/state 被拒绝。
- [ ] **Step 2：运行 `pytest tests/integration/test_scoped_permissions.py tests/integration/test_history_access.py -q` 与 `npx playwright test tests/e2e/oidc-session.spec.ts`，确认缺失接口/行为失败。**
- [ ] **Step 3：实现 BFF、权限和用户管理。**
  使用成熟 OIDC 客户端授权码＋PKCE；Token 只保存在后端加密会话，浏览器仅持 HttpOnly/Secure/SameSite cookie 与 CSRF Token；刷新并发串行化。平台用户以 issuer/sub 绑定稳定 UUID。Keycloak admin adapter 执行邀请/停用，普通用户密码不经过平台；邀请走内网 SMTP 或可审计单次激活链接，不伪装邮件成功。业务 roles/groups/grants 由 PG 维护，每项绑定主体、动作、范围/对象；授权版本变化使缓存和待执行请求重新校验。`resource_scopes` 注册通用对象范围，来源/证据/历史同样受控。`GET /session` 返回用户、显示名、权限摘要、授权版本及 csrf_token，不返回访问/刷新 Token。
  共享权限撤销是新修订，管理归属与服务关系不会自动扩权；Schema 权限独立。各新增/修改端点返回类型化详情，列表返回 `Page`；组成员变更、角色配置、授权更改均审计。
- [ ] **Step 4：运行上述检查及 F02 测试；另用无权限用户访问列表/计数/历史，验证不会暴露无权对象。**
- [ ] **Step 5：暂存本任务文件，提交 `feat: enforce OIDC sessions and scoped platform permissions`。**

### Task F04：不可变文件、证据元数据与安全访问

**Files**
- Create: `apps/api/alembic/versions/0003_artifacts.py`
- Create: `apps/api/src/lzo/artifacts/{store,models,schemas,service,router}.py`
- Test: `apps/api/tests/integration/test_artifacts.py`
- Modify: `apps/api/src/lzo/main.py`、`apps/api/tests/conftest.py`

**Interfaces**
- Consumes: F02 修订、F03 授权。
- Produces: 总计划 `ArtifactStore.put/open`；`attach_evidence(uow: UnitOfWork, actor: ActorContext, resource_id: UUID, blob: BlobRef, metadata: JsonObject) -> RevisionRef`。首版本地 `open` 返回支持 seek/read 的只读流，不把大包一次装入内存；未来对象存储提供等价 Range 适配。
- HTTP: `POST /artifacts`（流式上传）、`GET /artifacts/{version_id}`（元数据）、`GET /artifacts/{version_id}/content`（每次鉴权）、`POST /artifacts/{version_id}/links`、`POST /artifacts/{version_id}/reviews`；证据审核与实体采纳分开。

- [ ] **Step 1：写不可变、路径、权限和内容去重测试。**
  ```python
  def test_same_content_does_not_share_permissions(evidence_scenario):
      a, b = evidence_scenario.upload_same_bytes_to_two_scopes()
      assert a.sha256 == b.sha256
      assert a.version_id != b.version_id
      assert evidence_scenario.download_status("viewer_a", b.version_id) == 404
      assert evidence_scenario.path_traversal_status("../secret") in {400, 404, 422}
  ```
  本地 fixture 写入受控临时卷与真实元数据；覆盖 PDF/Word/图片保存原件、附件版本、单位用户配额错误、截断上传不注册完成、撤权后链接无法访问。
- [ ] **Step 2：执行 `pytest tests/integration/test_artifacts.py -q`，预期缺少服务失败。**
- [ ] **Step 3：实现摘要寻址文件与独立访问元数据。**
  文件先流式写临时对象，校验长度与摘要后原子完成；不信任文件名、扩展名或可猜测地址。相同字节可去重，权限不以摘要共享。流式上传在业务事务前完成，证据元数据/引用与调用方修订同事务登记，不让大文件传输占用长数据库事务。元数据绑定来源、版本、审核和对象范围；证据下载通过鉴权路由，响应安全内容类型与附件头，禁止公开持久 URL。S3 兼容实现通过同一 `ArtifactStore` 协议扩展。
- [ ] **Step 4：执行本任务及 F03 历史权限检查，确认内容与历史证据引用均可读回。**
- [ ] **Step 5：提交 `feat: store versioned evidence with scoped access`。**

### Task F05：持久作业、检查点与投影调度

**Files**
- Create: `apps/api/alembic/versions/0004_jobs.py`
- Create: `apps/api/src/lzo/worker/{models,service,runner,leases,health,router}.py`
- Test: `apps/api/tests/integration/test_job_recovery.py`
- Modify: `infra/compose/compose.yaml`、`apps/api/src/lzo/main.py`、`apps/api/tests/conftest.py`

**Interfaces**
- Consumes: F02 outbox/修订，F03 执行身份和当前授权。
- Produces: 总计划 `enqueue(...)`；`checkpoint(job_id: UUID, lease_token: UUID, cursor: JsonObject) -> None`；`set_job_state(actor: ActorContext, job_id: UUID, state: JobState, expected_revision: int) -> JobRef`。
- Produces: `register_handler(kind: str, handler: Callable[[JobContext], None]) -> None`；`JobContext(job_id, lease_token, actor: ActorContext, bundle_id: UUID | None, payload: JsonObject, cursor: JsonObject)`。
- Produces: `JobResultRef(kind: str, resource_id: UUID, api_path: str)` 和 `JobView(job: JobRef, revision: int, progress: JsonObject, result_ref: JobResultRef | None, error_code: str | None)`。GET `/jobs/{id}` 返回 JobView；异步预检/影响分析完成后写结果引用，api_path 只能是后端登记的 `/api/v1/...` 资源路径，读取目标仍重新鉴权，不能成为任意 URL 代理。
- HTTP: `GET /jobs`、`GET /jobs/{id}`、`POST /jobs/{id}/pause`、`POST /jobs/{id}/resume`、`POST /jobs/{id}/retry`；`GET /api/v1/health/overview` 仅运维授权可读。领域重算状态由 I05 管理，不把所有领域状态塞入 JobState。

- [ ] **Step 1：写租约、幂等与暂停恢复测试。**
  ```python
  def test_expired_worker_cannot_commit_checkpoint(job_scenario):
      old = job_scenario.claim_and_expire()
      new = job_scenario.reclaim()
      assert new.lease_token != old.lease_token
      assert job_scenario.checkpoint_error(old) == "LEASE_EXPIRED"
      job_scenario.pause_then_resume(new)
      assert job_scenario.completed_effect_count == 1
  ```
  定义 `job_scenario` 两个 Worker、真实 PG、可控制时钟和一个向测试表写一次结果的幂等 handler；覆盖事务回滚不入队、提交后崩溃重拾、重复 outbox 只投影一次、撤权任务不能继续、可选来源离线不使核心 ready 失败。
- [ ] **Step 2：执行 `pytest tests/integration/test_job_recovery.py -q`，确认失败。**
- [ ] **Step 3：实现 PG 队列、租约及执行边界。**
  使用事务领取与有界批次，检查租约与授权后执行；检查点、尝试次数、原因、心跳、错误及水位持久化。暂停停止新批次，已提交保留；重试有退避上限，永久失败进入可审计状态。投影 handler `rdf.project` 在 S01 注册；F05 不伪造语义结果。健康返回队列深度、投影水位、失败来源及受限指标，不泄露密码。
- [ ] **Step 4：执行本任务与 F02 并发事务检查，并实际终止/重启测试 Worker，验证任务继续或明确失败且无双重副作用。**
- [ ] **Step 5：提交 `feat: add recoverable jobs and fenced checkpoints`。**

### Task F06：风险分级、批准快照和执行前核验

**Files**
- Create: `apps/api/alembic/versions/0005_approvals.py`
- Create: `apps/api/src/lzo/governance/{risk,approvals,approval_schemas,approval_router}.py`
- Test: `apps/api/tests/integration/test_approval_gates.py`
- Modify: `apps/api/src/lzo/main.py`、`apps/api/src/lzo/auth/{schemas,permissions,router}.py`、`apps/api/tests/conftest.py`

**Interfaces**
- Consumes: F02 修订，F03 当前权限，F05 任务。
- Produces: `ApprovalRef(id: UUID, submitter_id: UUID, reviewer_id: UUID | None, risk: Literal["low","high"], change_digest: str, baseline: dict[str,int])`；`baseline` 键绑定资源/规则/输入水位标识，值为对应修订号。
- Produces: `ApprovalService.submit(actor: ActorContext, kind: str, target_ids: list[UUID], change_digest: str, baseline: dict[str,int], impact: JsonObject) -> ApprovalRef`。
- Produces: `ApprovalService.decide(actor, approval_id: UUID, decision: Literal["approve","reject"], comment: str) -> ApprovalRef`；`ApprovalService.require_executable(actor, approval_id: UUID, change_digest: str, baseline: dict[str,int]) -> ApprovalRef`。
- HTTP: `GET/POST /approvals`、`GET /approvals/{id}`、`POST /approvals/{id}/decisions`；列表按影响对象与独立 Schema 权限过滤。
- Integrates: 将 F03 角色/授权写命令接入本风险门：跨域扩权和重要操作授权携带 `approval_id`，与准确变更摘要绑定；普通授权管理仍按当前权限与审计执行。初始化测试/部署主体有独立受控步骤，不把安装例外暴露为普通 API 绕过入口。

- [ ] **Step 1：写独立审批与批准失效负例。**
  ```python
  def test_schema_addition_needs_other_reviewer_and_exact_baseline(approval_scenario):
      proposal = approval_scenario.submit("schema.add_class")
      assert proposal.risk == "high"
      assert approval_scenario.self_approve_error(proposal) == "SEPARATION_OF_DUTIES"
      approval_scenario.approve_as_other(proposal)
      approval_scenario.change_target_revision()
      assert approval_scenario.execute_error(proposal) == "APPROVAL_STALE"
  ```
  同文件定义 fixture 使用两名有权用户及第三名无权者；另测只有标签/描述且无语义影响才低风险直接发布、风险规则变更本身高风险、权限被撤销时旧批准不能执行、批量重算批准不允许跳过新增字段冲突裁决。
- [ ] **Step 2：执行 `pytest tests/integration/test_approval_gates.py -q`，确认失败。**
- [ ] **Step 3：实现可解释风险判定与不可变批准。**
  系统最低风险规则不能被编辑者降低；记录影响报告摘要、内容摘要、来源基线、意见、演员和时间。执行方重新核对全部绑定版本及当前权限；审批被替代或内容变化时要求新预览。保留多级审批扩展接口，首版只执行单级独立审核；不硬编码某个字段或农业场景。
- [ ] **Step 4：执行本任务和 F03/F05 检查；用两个真实账户验证不能自审新增类，批准后撤权阻止后台执行。**
- [ ] **Step 5：提交 `feat: gate risky changes with independent version-bound approval`。**

## 官方参考与版本验证

- OIDC 采用标准授权码、刷新与本地公钥验证；实现时使用成熟客户端并检查 issuer/audience 等约束。[Keycloak OIDC](https://www.keycloak.org/securing-apps/oidc-layers)
- Node 选择受支持的 LTS 分支，精确补丁以 F01 验证并锁定为准。[Node.js 发布表](https://nodejs.org/en/about/previous-releases)
- Windows 容器开发需验证实际 WSL/虚拟化及受支持宿主系统，不能仅凭 wsl.exe 存在判定可用。[Docker Windows 安装要求](https://docs.docker.com/desktop/setup/install/windows-install/)
- Fuseki 官方提供容器构建配置，数据需挂载持久路径，本文不采用其旧示例版本号。[Jena 容器说明](https://jena.apache.org/documentation/fuseki2/fuseki-docker.html)
- PG 行锁与 SQLAlchemy 事务控制是本计划的实现工具，应用仍须测试幂等和失败恢复。[PostgreSQL 锁](https://www.postgresql.org/docs/current/explicit-locking.html)、[SQLAlchemy 事务](https://docs.sqlalchemy.org/en/20/orm/session_transaction.html)
