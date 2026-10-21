# 多源接入、裁决与受控重算 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 以统一来源变更契约实现文件、PostgreSQL、MySQL 的可追溯接入、持续同步、字段裁决及受控历史重算。

**Architecture:** 连接器只读取并输出规范变更，映射和身份治理独立于来源协议。内部 PG 原始快照索引、候选、裁决和已采纳支持分别持久化；受控写入调用 S03，RDF 投影和推理由 S01/S04 消费公共 outbox，不另造图谱写通道。

**Tech Stack:** Python 3.12、Pydantic v2、SQLAlchemy/Alembic、PostgreSQL/MySQL 只读驱动、csv、openpyxl、JSON/GeoJSON、pytest；复用内部 PG、ArtifactStore、Fuseki/TDB2 与公共 Worker。

**Spec:** [已确认设计](../specs/2026-10-20-lzo-platform-p0-p1-design.md) §6–10、12、16；[总计划与公共契约](2026-10-20-lzo-p0-p1-implementation.md)。先读总计划及 F02–F06、S01–S04 对应接口。

## Global Constraints

- 首版用明确标记的合成数据，但使用真实连接器、数据库和持久化流程验证。
- 来源删除不能物理删除本体实体；支持失效、停用、历史保留和来源追踪。
- 全量导入、手动及定时增量同步，统一处理新增、修改、删除，预留 CDC 接口。
- 原始、候选、采纳、派生事实分开；人工采纳不自动将 `synthetic` 或 `imported_unverified` 变成 `verified`。
- 高风险提交人与审核人不同；历史不可原位改写；批量重算批准不替代逐项实质冲突审核。
- 角色、操作与范围绑定同一授权项；任务领取及每批提交前按当前权限重验，撤权后不能凭旧批准继续写。
- 核心功能断开外网仍正常运行；PDF、Word、图片首版只作证据，不接云解析或自动采纳。
- 只修改任务列明文件；不实现 P2 Action Engine、真实设备控制、实时 CDC 或任意脚本规则执行。

## Review Focus

1. CSV 编码、XLSX 公式/压缩炸弹和 JSON 非法结构：产生可定位错误，不执行公式或静默丢行；I01 负责。
2. 相同文件重放、同幂等键不同内容、同内容不同映射：区分无副作用重放、409 和新预检；I01/I04 负责。
3. 分页失败、过滤范围变化、同时间戳及乱序事件：不误删、不漏同时间戳记录、不以旧值覆盖新值；I03 负责。
4. 裁决依赖变化与来源失效：无关版本变化不废弃裁决，重要冲突复核，失效依据不继续驱动推理；I02/I06 负责。
5. 批次批准后被撤权或数据更新：暂停、重试和回滚重验权限/基线，不覆盖新修订；I05 负责。

## 目录、公共依赖与边界

`apps/api/src/lzo/ingestion/` 按职责划分：`schemas.py` 仅来源 DTO；`files.py` 解析；`evidence.py` 附件；`mapping.py` 规范化；`identity.py` 对齐；`rulings.py` 裁决；`connectors/` 只读来源；`sync.py` 扫描；`pipeline.py` 编排；`rules.py` 版本；`recalculation.py` 重算；`router.py` 受控 HTTP。`models.py` 与只追加迁移负责本模块 PG 表，不复制 S03 实体/断言表。

- 公共 `ActorContext / ResourceScope / RevisionRef / BlobRef / ExecutionBundle / JobRef / JobState / Page / JsonObject` 均从 F02 `lzo.contracts` 导入，不重定义。
- 所有内部写入消费 F02 `UnitOfWork`、`append_revision(uow, actor, resource_id, expected_revision, payload, event_type) -> RevisionRef`；服务不隐式 `commit()`，最外层命令一次提交业务、历史及 outbox。
- F03 `require_permission(actor, action, resource) -> None`；F04 `ArtifactStore.put(stream, media_type) -> BlobRef`、`.open(blob) -> BinaryIO`、`attach_evidence(uow, actor, resource_id, blob, metadata) -> RevisionRef`；不得用 blob 摘要代替业务附件授权。大文件可先上传，再用短事务登记引用；未引用 blob 按受控运维保留策略处理，不自行删除历史原件。
- F05 `enqueue(uow, actor, kind, payload, bundle_id) -> JobRef`、`checkpoint(job_id, lease_token, cursor) -> None`、`set_job_state(actor, job_id, state, expected_revision) -> JobRef`。
- F06 `ApprovalService.submit(actor, kind, target_ids, change_digest, baseline, impact) -> ApprovalRef`、`.require_executable(actor, approval_id, change_digest, baseline) -> ApprovalRef`、`.decide(actor, approval_id, decision, comment) -> ApprovalRef`；原样复用其类型与风险判级。
- S03 `validate_facts(actor, facts: tuple[AssertionInput, ...], bundle) -> ValidationReport`、`validate_object(actor, create: CreateObjectInput, bundle) -> ValidationReport`、`register_object(uow, actor, create: CreateObjectInput, bundle) -> WriteReceipt`、`apply_fact_patch(uow, actor, patch: FactPatch, bundle) -> WriteReceipt`、`write_geometry(uow, actor, geometry: GeometryInput, bundle) -> RevisionRef`。DTO 由 S03 定义；`RdfTerm` 从 S01 `lzo.semantic.rdf` 导入，其他输入/回执从 `lzo.semantic.schemas` 导入，不重复声明。
- S01 `project_revision(revision: RevisionRef) -> ProjectionReceipt` 消费上述写入产生的 outbox；成功后 S04 安排推理。I04 不调用裸 SPARQL、不重复 enqueue 图谱/推理任务；失败重试仍走公共 Worker。
- HTTP 使用总计划 `/api/v1`、`Idempotency-Key`、`If-Match`、401/403/404/409/422/428 和统一错误体。所有下述时间为带时区 UTC，UUID 为 JSON 字符串；DTO 为 Pydantic v2，拒绝未知字段。
- DTO 中 ID/修订/摘要/审核状态/水位等服务端字段在 OpenAPI 标记 readOnly，写入请求模型由任务列出的命令字段生成；不能通过客户端提交 `published/effective/verified` 绕过治理。各服务中的 GET/list 只读取当前有权结果，按总计划先授权再分页。
- 测试复用 F01 `db_session/api_client/compose_endpoints`、F02 `uow`、F03 `actor/other_actor/auditor`、F04 `artifacts`、F05 `jobs`、F06 `approvals`。本模块 fixture 位于 `apps/api/tests/integration/conftest_ingestion.py`，由同目录 `conftest.py` 显式加载；fixture 是真实服务/PG/MySQL 的测试装配，不是替代持久化的 mock。
- 迁移编号使用 `0201`–`0299`，实际实施时串行接入单一 Alembic head，`down_revision` 指向当时整合分支 head，不修改已经执行的迁移。依赖锁只更新 F01 `apps/api/requirements.lock`，不另建第二套锁。
- 下述 `pytest tests/...` 均在总计划 `api-test` 容器工作目录运行；完整包装为 `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/... -q`。提交命令在仓库根目录执行。

---

### I01：统一来源记录、结构化文件与受控证据附件

**Files:**
- Create: `apps/api/src/lzo/ingestion/schemas.py`, `apps/api/src/lzo/ingestion/files.py`, `apps/api/src/lzo/ingestion/evidence.py`, `apps/api/src/lzo/ingestion/extraction.py`, `apps/api/src/lzo/ingestion/router.py`, `apps/api/src/lzo/ingestion/models.py`。
- Create: `apps/api/alembic/versions/0201_ingestion_sources.py`；只建 `source_records / source_uploads`，附件身份/权限/链接复用 F04，追加修订复用 F02。
- Test: `apps/api/tests/unit/test_ingestion_files.py`, `apps/api/tests/integration/test_ingestion_evidence.py`, `apps/api/tests/integration/conftest_ingestion.py`, `apps/api/tests/integration/conftest.py`。

**Interfaces — consumes:** F02 公共类型/UoW/修订；F03 权限；F04 ArtifactStore、附件 metadata/links/reviews 与 `attach_evidence`。证据写入 `evidence.manage`，下载 `evidence.read`，同时检查关联对象/来源范围。I01 的 EvidenceService 是来源元数据 facade，不建另一套附件权限或二进制存储。

**Interfaces — produces:** `schemas.py` 定义下列 DTO，不把来源内容塞入公共 `contracts.py`：

```python
DataNature = Literal["synthetic", "test", "imported_unverified", "verified"]
SourceKey(source_id: UUID, source_system: str, source_instance: str, collection: str, canonical_key: str)
SourcePosition(stream_id: str, epoch: UUID, ordinal: int, source_version: str | None, cursor: JsonObject)
SourceChange(change_id: UUID, key: SourceKey, operation: Literal["upsert", "delete"],
             position: SourcePosition, valid_from: datetime | None, valid_to: datetime | None,
             observed_at: datetime | None, received_at: datetime, snapshot: BlobRef,
             content_sha256: str, idempotency_key: str, raw_fields: JsonObject,
             geometry: JsonObject | None, data_nature: DataNature, locator: str)
FileOptions(format: Literal["csv", "xlsx", "json", "geojson"], encoding: str,
            delimiter: str, sheet: str | None, header_row: int, records_path: str,
            key_paths: tuple[str, ...], source_crs: str | None, source_timezone: str | None,
            data_nature: DataNature)
ParseIssue(locator: str, field_path: str | None, code: str, message: str)
ParsedFile(changes: tuple[SourceChange, ...], issues: tuple[ParseIssue, ...], complete: bool)
EvidenceCreate(source_id: UUID | None, scope: ResourceScope, target_refs: tuple[RevisionRef, ...],
               title: str, locator: str | None, data_nature: DataNature, replaces_id: UUID | None)
EvidenceView(id: UUID, revision: RevisionRef, blob: BlobRef, source_id: UUID | None,
             scope: ResourceScope, target_refs: tuple[RevisionRef, ...], title: str,
             locator: str | None, data_nature: DataNature, review_state: Literal["candidate", "accepted", "rejected"],
             replaces_id: UUID | None, created_at: datetime)
FileParser.parse(blob: BlobRef, key_template: SourceKey, options: FileOptions) -> ParsedFile
EvidenceService.create(uow: UnitOfWork, actor: ActorContext, stream: BinaryIO,
                       media_type: str, command: EvidenceCreate) -> EvidenceView
EvidenceService.read(actor: ActorContext, evidence_id: UUID) -> EvidenceView
ExtractionCandidate(source_blob: BlobRef, locator: str, model_id: str, model_version: str,
                    proposed_subject: str | None, predicate_iri: str, proposed_object: JsonObject,
                    confidence: float | None, extracted_at: datetime)
DocumentExtractor.extract(blob: BlobRef) -> Iterator[ExtractionCandidate]
```

`extraction.py` 仅声明以上候选 DTO 与 Protocol，不接 AI、不实现抽取服务、不开放假成功端点；后续插件输出只能进入同一候选/证据/审核链，confidence 不是事实概率。本轮不为尚无实现的抽取协议新增运行测试。

`key_template.canonical_key` 在每行按 `key_paths` 生成；多字段键采用规范 JSON 编码，不拼接易碰撞分隔符。文件 position.epoch 绑定已登记上传版本，locator 和内容摘要稳定参与来源事件幂等键，不用本次接收时间制造新事件。同来源、同内容、同解析配置复用上传记录；同幂等键不同字节报 409。data_nature 从受控来源配置取值，不接受客户端自行提升为 verified。物理 blob 去重不合并不同范围的证据身份；同名文件内容改变产生新版本。PDF/DOCX/图片只存原件，不执行宏或解析远程引用。

HTTP：`POST /artifacts/evidence` multipart `file`＋`EvidenceCreate` 元数据，operationId `createEvidence`，201 `EvidenceView`；`GET /artifacts/evidence/{id}` 为 `getEvidence`，200 `EvidenceView`。静态 evidence 路径注册在 F04 动态 `/{version_id}` 路由之前，或使用明确 UUID 路径转换器，避免把 evidence 当作版本 ID。二进制由 F04 已受控下载路由消费其 `blob`，不能暴露永久公共文件 URL。

- [ ] **Step 1: 写失败测试。** I01 fixture `file_case` 提供真实 blob、同一 `SourceKey`、四种 `options`；`evidence_case` 提供不同范围 actor 与附件命令，所有字段由上述 DTO 构建。

```python
@pytest.mark.parametrize("fmt", ["csv", "xlsx", "json", "geojson"])
def test_all_files_emit_same_source_contract(file_case, fmt):
    result = file_case.parser.parse(file_case.blobs[fmt], file_case.key, file_case.options[fmt])
    assert result.complete and not result.issues
    assert [c.key.canonical_key for c in result.changes] == ['["POND-001"]']
    assert result.changes[0].raw_fields["name"] == "合成水塘"
    assert result.changes[0].data_nature == "synthetic"

def test_invalid_or_oversized_files_fail_locally(file_case):
    for blob, code in file_case.invalid_inputs:
        parsed = file_case.parser.parse(blob, file_case.key, file_case.options["xlsx"])
        assert not parsed.complete and code in {e.code for e in parsed.issues}
    assert file_case.accepted_entity_count() == 0
```

另写 `test_evidence_hash_dedup_does_not_grant_cross_scope_access`、`test_same_upload_key_changed_bytes_conflicts`：用真实 API 断言同名新内容保留两版、同摘要不同范围 metadata ID 不同、无权读取返回404且无摘要/位置泄露。无编码配置的损坏字节、XLSX 公式单元格与超压缩限额各有错误定位；不计算公式缓存值作为确定事实。

- [ ] **Step 2: 跑红。** `pytest tests/unit/test_ingestion_files.py tests/integration/test_ingestion_evidence.py -q`；预期缺少 `FileParser/EvidenceService` 或断言失败，不得因缺真实 PG 被计为通过。
- [ ] **Step 3: 最小实现。** `files.py` 用标准解析器及 openpyxl 只读模式，强制显式公式处理、工作表/表头/记录路径、空值/类型错误和可配置资源限额；GeoJSON 保留原坐标与 CRS，不猜偏移。证据 metadata 与修订在 UoW 内写入；预检允许保存原件和 staging，不能创建已采纳对象/关系。
- [ ] **Step 4: 跑绿。** 重跑上述命令；四种文件、证据授权和幂等负例全部 PASS，确认日志不含附件敏感内容；锁定新增解析依赖并更新 API 快照。
- [ ] **Step 5: 小提交。** `git add apps/api/src/lzo/ingestion apps/api/alembic/versions/0201_ingestion_sources.py apps/api/tests/unit/test_ingestion_files.py apps/api/tests/integration/test_ingestion_evidence.py apps/api/tests/integration/conftest_ingestion.py apps/api/tests/integration/conftest.py apps/api/pyproject.toml apps/api/requirements.lock contracts/openapi.json`；`git commit -m "feat: normalize file sources and preserve scoped evidence"`。

### I02：版本化映射、稳定身份、字段权属与裁决有效性

**Files:**
- Create: `apps/api/src/lzo/ingestion/mapping.py`, `apps/api/src/lzo/ingestion/identity.py`, `apps/api/src/lzo/ingestion/rulings.py`, `apps/api/src/lzo/ingestion/sources.py`。
- Modify: `apps/api/src/lzo/ingestion/schemas.py`, `apps/api/src/lzo/ingestion/models.py`, `apps/api/src/lzo/ingestion/router.py`。
- Create: `apps/api/alembic/versions/0202_ingestion_mapping_rulings.py`；来源绑定、对齐键、映射版本、候选、冲突、裁决及依赖评估表。
- Test: `apps/api/tests/unit/test_ingestion_mapping.py`, `apps/api/tests/integration/test_ingestion_identity_rulings.py`；扩充本模块 fixture。

**Interfaces — consumes:** I01 DTO；F02–F04；F06 审批；S03 `AssertionInput / RdfTerm / SourceSupport`。映射/身份匹配本身不写已采纳语义事实；合并另经受控命令，不以同名或 `owl:sameAs` 自动触发。

**Interfaces — produces:** 所有规则结构拒绝任意代码和非白名单操作符；`RuleExpr` 递归结构由本任务声明：

```python
RuleExpr(op: Literal["path", "literal", "coalesce", "cast", "unit", "date", "normalize", "eq", "and", "or"],
         args: tuple[RuleExpr, ...], path: str | None, value: JsonObject | None)
AlignmentRule(id: UUID, digest: str, class_iri: str, key_paths: tuple[str, ...],
              normalizers: tuple[RuleExpr, ...], scope: ResourceScope, confirmed: bool)
AuthorityRule(predicate_iri: str, owner_policy: Literal["ontology_owned", "source_backed", "derived", "read_only"],
              owner_source_ids: tuple[UUID, ...], source_priority: tuple[UUID, ...],
              comparable_time_path: str | None, important: bool, digest: str)
RulingPolicyRule(predicate_iris: tuple[str, ...], expression: RuleExpr, always_review: bool, digest: str)
FieldMap(source_path: str, predicate_iri: str, datatype_iri: str | None,
         source_unit: str | None, target_unit: str | None, expression: RuleExpr | None)
MappingProfile(id: UUID, source_id: UUID, revision: int, class_iri: str,
               identity_paths: tuple[str, ...], confirmed_identity_rule_id: UUID | None,
               name_path: str, fields: tuple[FieldMap, ...], geometry_path: str | None,
               source_crs: str | None, source_timezone: str | None, digest: str)
SourceCreate(source_system: str, source_instance: str, collection: str,
             kind: Literal["file", "postgresql", "mysql"], scope: ResourceScope, data_nature: DataNature)
SourceView(id: UUID, revision: int, configuration: SourceCreate, mapping_version_id: UUID | None,
           connection_revision: int | None, schedule_revision: int | None, last_success_at: datetime | None)
IdentityMatch(state: Literal["existing", "new", "candidate"], resource_id: UUID | None,
              matched_rule_id: UUID | None, binding_revision: int | None, explanation: str)
DependencyBinding(entity_id: UUID | None, source_keys: tuple[SourceKey, ...],
                  predicate_iris: tuple[str, ...], support_ids: tuple[UUID, ...],
                  evidence_digests: tuple[str, ...], valid_from: datetime | None, valid_to: datetime | None,
                  authority_rule_digest: str, ruling_rule_digest: str, schema_dependency_digest: str, digest: str)
CandidateValue(id: UUID, entity_id: UUID | None, predicate_iri: str | None, raw_value: JsonObject,
               normalized_value: RdfTerm | None, source_key: SourceKey, snapshot: BlobRef,
               locator: str, evidence_refs: tuple[RevisionRef, ...], bundle_id: UUID,
               source_version: str | None, observed_at: datetime | None,
               valid_from: datetime | None, valid_to: datetime | None, data_nature: DataNature)
ConflictView(id: UUID, revision: int, scope: ResourceScope, entity_id: UUID | None,
             predicate_iri: str | None, candidate_ids: tuple[UUID, ...], candidate_values: tuple[CandidateValue, ...],
             dependencies: DependencyBinding,
             state: Literal["open", "resolved", "needs_review", "withdrawn"], reason: str)
RulingCommand(expected_revision: int, chosen_candidate_ids: tuple[UUID, ...],
              rejected_candidate_ids: tuple[UUID, ...], rationale: str, approval_id: UUID)
RulingView(id: UUID, revision: RevisionRef, conflict_id: UUID, dependencies: DependencyBinding,
           chosen_candidate_ids: tuple[UUID, ...], rejected_candidate_ids: tuple[UUID, ...],
           state: Literal["effective", "needs_review", "superseded"], rationale: str,
           reviewer_id: UUID, decided_at: datetime, replaces_id: UUID | None)
MergePreviewCommand(canonical_id: UUID, duplicate_ids: tuple[UUID, ...])
MergeCommand(proposal_id: UUID, approval_id: UUID)
MergeProposal(id: UUID, revision: int, canonical_id: UUID, duplicate_ids: tuple[UUID, ...],
              scopes: tuple[ResourceScope, ...], baseline: dict[str, int], conflict_ids: tuple[UUID, ...],
              evidence_refs: tuple[RevisionRef, ...], change_digest: str, approval_id: UUID)
MappedRecord(change: SourceChange, identity: IdentityMatch, class_iri: str, display_name: str,
             scope: ResourceScope, facts: tuple[AssertionInput, ...], geometry: JsonObject | None,
             conflicts: tuple[ConflictView, ...], decision_ref: RevisionRef | None)
SourceService.create(uow: UnitOfWork, actor: ActorContext, command: SourceCreate) -> SourceView
SourceService.save_mapping(uow: UnitOfWork, actor: ActorContext, profile: MappingProfile) -> MappingProfile
IdentityService.resolve(uow: UnitOfWork, actor: ActorContext, change: SourceChange,
                        profile: MappingProfile, reserve: bool) -> IdentityMatch
IdentityService.preview_merge(actor: ActorContext, canonical_id: UUID, duplicate_ids: tuple[UUID, ...]) -> MergeProposal
IdentityService.merge(uow: UnitOfWork, actor: ActorContext, proposal_id: UUID, approval_id: UUID) -> RevisionRef
MappingService.plan(actor: ActorContext, change: SourceChange, profile: MappingProfile,
                    bundle: ExecutionBundle, identity: IdentityMatch) -> MappedRecord
RulingService.resolve(uow: UnitOfWork, actor: ActorContext, conflict_id: UUID, command: RulingCommand) -> RulingView
RulingService.reevaluate(uow: UnitOfWork, actor: ActorContext, ruling_id: UUID,
                         dependencies: DependencyBinding, has_material_conflict: bool) -> RulingView
```

HTTP：`GET/POST /sources` → `listSources/createSource`，`Page[SourceView]/SourceView`；`GET /sources/{id}` → `getSource`，`SourceView`；`GET/PUT /sources/{id}/mapping` → `getSourceMapping/saveSourceMapping`，`MappingProfile`。`GET /conflicts` → `listConflicts`，`Page[ConflictView]`；`GET /conflicts/{id}` → `getConflict`，`ConflictView`；`GET/POST /conflicts/{id}/rulings` → `listConflictRulings/resolveConflict`，`Page[RulingView]/RulingView`。冲突列表逐字段带原始/规范值和证据，详情通过已授权候选/历史引用读取，不仅返回来源名称。

合并 HTTP：`POST /identity/merges/preview` → `previewIdentityMerge`，输入 `MergePreviewCommand`，201 `MergeProposal`；`POST /identity/merges` → `mergeIdentities`，输入 `MergeCommand`，200 `RevisionRef`。两个主体的管理权限及范围都必须满足，F06 永远按高风险独立审核，执行时重新核对全部基线和当前权限。保留 canonical 的单一主管理归属，归属矛盾进入影响报告，不默默转移；合并不合并/复制授权，旧 alias 解析亦重验当前目标权限。U04 在身份冲突页复用这两个端点。

- [ ] **Step 1: 写失败测试。** `mapping_case` 提供映射服务、同业务键的两源变更、仅同名但不同身份变更；`ruling_case` 提供真实 PG 中已生效裁决和相关/无关依赖版本。

```python
def test_stable_identity_requires_confirmed_business_key(mapping_case, uow, actor):
    first = mapping_case.resolve(uow, actor, mapping_case.pg_change, reserve=True)
    second = mapping_case.resolve(uow, actor, mapping_case.mysql_change, reserve=True)
    ambiguous = mapping_case.resolve(uow, actor, mapping_case.name_only_change, reserve=True)
    assert first.resource_id == second.resource_id
    assert ambiguous.state == "candidate" and ambiguous.resource_id is None

def test_ruling_reuses_only_unchanged_material_dependencies(ruling_case, uow, actor):
    before = ruling_case.ruling
    same = ruling_case.service.reevaluate(uow, actor, before.id, ruling_case.unrelated_change, False)
    changed = ruling_case.service.reevaluate(uow, actor, before.id, ruling_case.new_conflicting_evidence, True)
    assert same.id == before.id and same.state == "effective"
    assert changed.state == "needs_review"
    assert ruling_case.original_revision_payload() == ruling_case.saved_original_payload
```

fixture `mapping_case.resolve` 仅按绑定的 profile 调用上文 `IdentityService.resolve`，不另实现身份算法。加 `test_unit_conversion_respects_owner_policy`：源自有字段转换后可提议采纳；`ontology_owned` 备注保持原值，`derived` 不可被导入覆盖。加 `test_expired_last_support_withdraws_even_while_ruling_pending`：旧证据失效且无其他支持时，当前事实撤回，历史裁决仍可查；有另一有效支持时不误撤。

```python
def test_merge_preserves_identity_history_and_stale_approval_is_rejected(merge_case):
    proposal = merge_case.identity.preview_merge(merge_case.actor, merge_case.keep_id, (merge_case.old_id,))
    merge_case.approve_as_other(proposal.approval_id)
    merge_case.concurrent_edit_duplicate()
    with pytest.raises(Conflict) as exc:
        merge_case.identity.merge(merge_case.uow, merge_case.actor, proposal.id, proposal.approval_id)
    assert exc.value.code == "APPROVAL_STALE"
    merged = merge_case.repreview_approve_and_merge()
    assert merged.resource_id == merge_case.keep_id
    assert merge_case.old_iri_resolves_to_canonical_without_reuse()
    assert merge_case.original_revisions_and_support_origins_exist()
    assert merge_case.conflicting_field_remains_pending_review()
```

- [ ] **Step 2: 跑红。** `pytest tests/unit/test_ingestion_mapping.py tests/integration/test_ingestion_identity_rulings.py -q`；预期缺少规则/裁决服务或上述行为断言失败。
- [ ] **Step 3: 最小实现。** 来源完整键有唯一约束；跨源确认业务键另有可审计唯一索引及竞争检查，不用名称/距离自动合并。`reserve=False` 仅试算；`reserve=True` 在应用 UoW 中锁定绑定，冲突重读或报告409。合并按 UUID 稳定顺序锁定资源，原 UUID/IRI 不删除、不复用，追加 alias/merged_into 和支持重绑出处；事实迁移调用 S03，字段冲突继续审核，不强覆写，防止循环或重复合并。单位/时间转换显式记录；裁决依据比较相关字段/支持/权属规则摘要而非整个 bundle 版本，无法判断即复核；复核结果和替代裁决均追加历史。
- [ ] **Step 4: 跑绿。** 重跑上述命令及 I01 测试；确认数据性质独立于审核状态、两来源共享身份但保留两份支持、源失效不删除平台备注；更新 OpenAPI。
- [ ] **Step 5: 小提交。** `git add apps/api/src/lzo/ingestion apps/api/alembic/versions/0202_ingestion_mapping_rulings.py apps/api/tests/unit/test_ingestion_mapping.py apps/api/tests/integration/test_ingestion_identity_rulings.py apps/api/tests/integration/conftest_ingestion.py contracts/openapi.json`；`git commit -m "feat: version source mappings identity and evidence-bound rulings"`。

### I03：真实 PostgreSQL/MySQL、持续同步与可靠删除屏障

**Files:**
- Create: `apps/api/src/lzo/ingestion/connectors/base.py`, `apps/api/src/lzo/ingestion/connectors/postgresql.py`, `apps/api/src/lzo/ingestion/connectors/mysql.py`。
- Create: `apps/api/src/lzo/ingestion/credentials.py`, `apps/api/src/lzo/ingestion/sync.py`, `apps/api/src/lzo/ingestion/scheduler.py`。
- Modify: `apps/api/src/lzo/ingestion/schemas.py`, `apps/api/src/lzo/ingestion/sources.py`, `apps/api/src/lzo/ingestion/models.py`, `apps/api/src/lzo/ingestion/router.py`；`infra/compose/compose.yaml` 配置 F01 已创建的 `source-postgres/source-mysql` 测试源，不重复建立同名服务。
- Create: `apps/api/alembic/versions/0203_ingestion_scans_schedules.py`；扫描成员、检查点、来源位置、定时配置及加密来源凭据表。
- Test: `apps/api/tests/integration/test_ingestion_database_sources.py`, `apps/api/tests/integration/test_ingestion_scan_deletes.py`；扩充本模块 fixture。

**Interfaces — consumes:** I01 `SourceChange/SourcePosition`；I02 `SourceView/MappingProfile`；F05 enqueue/checkpoint/租约；外部源只读凭据，平台事务库与来源连接绝不共用用户。

**Interfaces — produces:**

```python
SourceCredentialInput(username: str, password: SecretStr, expected_revision: int)
CredentialRef(ref: str, source_id: UUID, revision: int)
SourceCredentialStore.set(uow: UnitOfWork, actor: ActorContext, source_id: UUID,
                          command: SourceCredentialInput) -> CredentialRef
SourceCredentialStore.resolve(credential_ref: str) -> tuple[str, SecretStr]  # 仅可信连接器内部调用。
ConnectionConfig(source_id: UUID, expected_revision: int, host: str, port: int,
                 database: str, schema_name: str | None, table_name: str,
                 credential_ref: str, tls_mode: Literal["verify", "internal_trust"],
                 key_columns: tuple[str, ...], updated_at_column: str | None,
                 deleted_column: str | None, filter_expression: RuleExpr | None)
ConnectionCheck(source_id: UUID, reachable: bool, read_only: bool,
                incremental_mode: Literal["timestamp_key", "snapshot_diff"], issues: tuple[ParseIssue, ...])
ReadRequest(source_id: UUID, mode: Literal["full", "incremental"], cursor: JsonObject | None,
            page_size: int, scope_digest: str, upper_fence: JsonObject | None)
SourcePage(changes: tuple[SourceChange, ...], next_cursor: JsonObject | None,
           seen_keys: tuple[str, ...], source_snapshot_token: str, end_of_scan: bool)
ScanResult(id: UUID, source_id: UUID, scope_digest: str, snapshot_token: str,
           state: Literal["running", "complete", "partial", "failed"], read_count: int,
           cursor: JsonObject | None, last_success_watermark: JsonObject | None, errors: tuple[ParseIssue, ...])
ScheduleCommand(source_id: UUID, expected_revision: int, enabled: bool, interval_seconds: int)
ScheduleView(source_id: UUID, revision: int, enabled: bool, interval_seconds: int, next_run_at: datetime | None)
SourceConnector.check(config: ConnectionConfig) -> ConnectionCheck
SourceConnector.read_page(config: ConnectionConfig, request: ReadRequest) -> SourcePage
SyncService.scan(actor: ActorContext, request: ReadRequest, job_id: UUID, lease_token: UUID) -> ScanResult
SyncService.deletions(actor: ActorContext, scan_id: UUID, previous_scan_id: UUID) -> tuple[SourceChange, ...]
Scheduler.tick(now: datetime) -> tuple[JobRef, ...]
```

连接器返回原始记录、位置和快照，不裁决业务字段；`SecretStr` 使用 Pydantic 类型，密码只写不回显。凭据通过 `sources.credentials.manage` 和来源范围校验，使用部署注入主密钥的认证加密存储，或可替换 Secret provider；不要求管理员手改数据库。resolve 仅连接器内部使用，每次调用前服务层校验当前任务授权。审计只记凭据引用/修订，不记录密码、密文或可猜测密码摘要；含秘密的幂等内容比较使用服务端密钥 HMAC，不能对掩码字符串做摘要。备份包括密文及受控密钥恢复流程，不把密钥写进普通 Git/日志。表/列经元数据白名单校验，过滤采用绑定参数/结构化表达式，不接受上传 SQL。CDC 以后仅实现同一 `SourceConnector`/`SourceChange` 契约，不在本任务部署日志订阅。

HTTP：`PUT /sources/{id}/credentials` → `setSourceCredentials`，输入 `SourceCredentialInput`，输出 `CredentialRef`，密码在 OpenAPI 标记 writeOnly；`GET/PUT /sources/{id}/connection` → `getSourceConnection/configureSourceConnection`，返回脱敏 `ConnectionConfig`；`POST /sources/{id}/test` → `testSourceConnection`，`ConnectionCheck`；`GET/PUT /sources/{id}/schedule` → `getSourceSchedule/configureSourceSchedule`，`ScheduleView`。手动同步复用 I04 `/imports/preview`＋`/imports/apply`；定时任务保存配置修订、发起/执行身份并走相同管线，不伪造人工确认。

- [ ] **Step 1: 写失败测试。** `database_case` 参数化 `postgresql/mysql`，提供真实来源 admin seed 连接、只读 connector、`ReadRequest`、真实历史扫描；`scan_case` 的断连注入只在真实第一页返回后让下一次网络读取失败。

```python
@pytest.mark.parametrize("backend", ["postgresql", "mysql"])
def test_full_and_incremental_read_use_stable_composite_cursor(database_case, backend):
    case = database_case(backend)
    first = case.connector.read_page(case.config, case.same_timestamp_request)
    second = case.connector.read_page(case.config, case.request_after(first))
    assert {c.key.canonical_key for c in first.changes + second.changes} == case.seeded_keys
    assert case.check().read_only

@pytest.mark.parametrize("condition", ["page_failure", "partial_keys", "scope_changed", "snapshot_lost"])
def test_incomplete_or_different_scan_never_infers_deletes(scan_case, condition):
    scan = scan_case.run(condition)
    with pytest.raises(Conflict, match="SCAN_NOT_COMPARABLE"):
        scan_case.service.deletions(scan_case.actor, scan.id, scan_case.previous.id)
    assert scan_case.existing_supports_are_active()
```

加 `test_complete_same_scope_scan_generates_only_missing_keys`、`test_old_event_cannot_overwrite_new_source_revision`、`test_scheduler_two_ticks_enqueue_once`：完整同范围扫描才产生确实缺失键的 delete；旧版本不退水位，同键不同内容幂等冲突；多调度进程竞争同一到期窗口只产一份作业。`test_source_credentials_are_writeonly_encrypted_and_scoped` 断言真实连接成功、HTTP/日志/修订均无密码、PG密文字段不含原值、另一区域不能获取/替换引用，错误恢复密钥显式失败而非回退明文。

- [ ] **Step 2: 跑红。** `pytest tests/integration/test_ingestion_database_sources.py tests/integration/test_ingestion_scan_deletes.py -q`；PG/MySQL 两个参数都必须实际连接，依赖不可用应失败并记录原因。
- [ ] **Step 3: 最小实现。** 一次完整扫描持有来源一致性快照；PG 只读 repeatable-read、MySQL 只读一致快照及稳定复合主键分页。快照丢失不得跨连接续作同次完整扫描。增量使用更新时间＋主键复合游标与可配置重叠窗口，并按内容/位置去重；没有可靠依据就全量快照对账。`ordinal` 只在同 epoch 内排序，跨 epoch 必须按已配置的可靠源版本/时间比较，不能按导入先后覆盖；无法判断新旧则重读对账/冲突审核。`complete` 需同 scope digest、所有页/键读取成功、快照有效；mapping 过滤或数据范围改变不能制造源删除。普通增量不凭“未出现”推断删除；仅显式删除标志/可靠 delete 事件或完整扫描差分产生删除。
- [ ] **Step 4: 跑绿。** 重跑上述命令及 I02；检查断线不推进 last_success_watermark、部分结果不清空现有支持、单源错误不使核心 ready 失败；更新 Compose 版本锁与 OpenAPI。
- [ ] **Step 5: 小提交。** `git add apps/api/src/lzo/ingestion apps/api/alembic/versions/0203_ingestion_scans_schedules.py apps/api/tests/integration/test_ingestion_database_sources.py apps/api/tests/integration/test_ingestion_scan_deletes.py apps/api/tests/integration/conftest_ingestion.py infra/compose/compose.yaml infra/versions.lock.json apps/api/pyproject.toml apps/api/requirements.lock contracts/openapi.json`；`git commit -m "feat: add readonly database sync with safe snapshot reconciliation"`。

### I04：统一预检、正式应用、来源支持与 RDF 投影水位

**Files:**
- Create: `apps/api/src/lzo/ingestion/pipeline.py`, `apps/api/src/lzo/ingestion/worker.py`。
- Modify: `apps/api/src/lzo/ingestion/schemas.py`, `apps/api/src/lzo/ingestion/models.py`, `apps/api/src/lzo/ingestion/router.py`, `apps/api/src/lzo/worker/handlers.py`；公共 Worker 仅注册本模块 handler，不重写租约/状态机。
- Create: `apps/api/alembic/versions/0204_ingestion_runs_rows.py`；预检、逐行结果、输入版本快照和支持关联表。
- Test: `apps/api/tests/integration/test_ingestion_pipeline.py`, `apps/api/tests/integration/test_ingestion_projection.py`；扩充本模块 fixture。

**Interfaces — consumes:** I01–I03；S03 上文写入/校验入口；S01 `ProjectionReceipt`；F02 幂等、事务、修订/outbox；F05 持久任务。断言 `SourceSupport` 使用 S03 已定义的 snapshot、原值、映射版本、evidence_refs、observed_at；`CreateObjectInput.decision_ref` 引用同样不可变的裁决/确定规则评估，不先创建空实体规避校验。

**Interfaces — produces:**

```python
ImportSelection(source_id: UUID, upload: BlobRef | None, file_options: FileOptions | None,
                mode: Literal["full", "incremental"], scan_id: UUID | None,
                semantic_format: Literal["turtle", "json-ld"] | None,
                semantic_kind: Literal["schema", "shapes", "instances"] | None,
                base_release_id: UUID | None, target_draft_id: UUID | None, target_draft_revision: int | None)
ImportPreviewCommand(selection: ImportSelection, bundle_id: UUID)
RowResult(locator: str, source_key: SourceKey, state: Literal["ready", "needs_review", "invalid", "applied", "failed", "unchanged"],
          entity_id: UUID | None, revision: RevisionRef | None, issue_refs: tuple[ParseIssue, ...],
          conflict_ids: tuple[UUID, ...], assertion_ids: tuple[UUID, ...])
PreviewReport(id: UUID, revision: int, selection: ImportSelection, bundle: ExecutionBundle,
              input_digest: str, baseline: dict[str, int], rows: tuple[RowResult, ...],
              ready_count: int, review_count: int, invalid_count: int, complete: bool,
              change_digest: str, approval_id: UUID | None)
ApplyImportCommand(preview_id: UUID, expected_revision: int, input_digest: str, approval_id: UUID | None)
ImportRunView(id: UUID, job: JobRef, preview_id: UUID, bundle_id: UUID,
              read_count: int, applied_count: int, review_count: int, failed_count: int,
              schema_draft_id: UUID | None,
              projection_watermark: int | None, inference_watermark: int | None,
              projection_state: Literal["pending", "synced", "failed"], complete: bool)
IngestionService.preview(actor: ActorContext, command: ImportPreviewCommand) -> PreviewReport
IngestionService.apply(uow: UnitOfWork, actor: ActorContext, command: ApplyImportCommand) -> JobRef
IngestionService.process_row(uow: UnitOfWork, actor: ActorContext, row: MappedRecord,
                            bundle: ExecutionBundle, expected_revision: int) -> RowResult
IngestionService.run_batch(actor: ActorContext, job_id: UUID, lease_token: UUID) -> ImportRunView
```

HTTP：`POST /imports/preview` → `previewImport`，201 `PreviewReport`；`POST /imports/apply` → `applyImport`，202 `JobRef`；`GET /imports/{id}` → `getImport`，`ImportRunView`（id＝apply返回的job.id）；`GET /imports/{id}/rows` → `listImportRows`，`Page[RowResult]`。`GET /imports/previews/{id}` → `getImportPreview`，`PreviewReport`；`GET /imports/previews/{id}/rows` → `listImportPreviewRows`，`Page[RowResult]`。完整逐行明细持久化并分页，`PreviewReport.rows` 仅第一页样本，计数按全部授权记录计算；大预检由 F05 202 job 执行，作业详情提供结果资源引用，不能塞无限响应。

- [ ] **Step 1: 写失败测试。** `pipeline_case` 组装真实映射/身份/PG/S03/Fuseki 与公共 worker，保存原始、候选、采纳三层计数；它的 `preview_command/apply_command` 均是上述 DTO，`drain_workers()` 只推进已登记真实 handler。

```python
def test_preview_then_apply_is_idempotent_and_version_bound(pipeline_case, uow, actor):
    report = pipeline_case.service.preview(actor, pipeline_case.preview_command)
    assert pipeline_case.accepted_count() == 0 and pipeline_case.fact_outbox_count() == 0
    first = pipeline_case.service.apply(uow, actor, pipeline_case.apply_command(report))
    again = pipeline_case.service.apply(uow, actor, pipeline_case.apply_command(report))
    assert first.id == again.id
    pipeline_case.commit_and_drain()
    assert pipeline_case.entity_count() == 1 and pipeline_case.active_support_count() == 1
    assert pipeline_case.query_sensor_target() == pipeline_case.expected_target_iri

def test_candidate_and_rejected_rows_never_become_accepted_by_projection(pipeline_case):
    run = pipeline_case.run_mixed_valid_conflicting_invalid_rows()
    assert run.applied_count == 1 and run.review_count == 1 and run.failed_count == 1
    assert not run.complete
    assert pipeline_case.default_graph_candidates() == []
    assert pipeline_case.row_issues()[0].locator and pipeline_case.raw_snapshots_exist()
```

再写 `test_pg_commit_rdf_failure_retries_same_revision_without_duplicate_support`：真实 Fuseki 暂停后 PG 已提交，状态 pending/failed 可见；恢复后调用 S01 幂等消费同 revision，最终只有一份支持和正确水位。`test_same_bytes_new_mapping_requires_new_preview`、`test_same_idempotency_key_changed_digest_returns_409` 覆盖版本去重和内容冲突。

语义文件复用 S01 `semantic/files.py::parse_semantic_document(blob, format) -> ParsedSemanticDocument`，DTO 的 `dataset/artifact/format` 由 S01 定义。禁止远程 JSON-LD context、OWL imports 自动下载。schema/shapes 应用时调用 S02 `ontology/drafts.py::import_semantic_draft(uow, actor, request: SemanticDraftImport) -> SchemaDraftView`；其 DTO 字段 `blob/format/kind/base_release_id/target_draft_id/expected_revision` 从 ImportSelection 构造，`target_draft_id=None, expected_revision=0` 建新草稿，否则更新目标草稿；结果 `schema_draft_id` 进入 ImportRunView。S02 自己持有 parser 和草稿 API，不能反向依赖 I04；预检不激活发布版本。instances 按主体及原图转为 SourceChange，保留来源 IRI、命名图、原件摘要和 bnode 来源作用域；bnode 作用域按来源＋文档版本＋规范化局部标识登记，不跨文件错误合并，`owl:sameAs` 只能提议对齐。

```python
@pytest.mark.parametrize("fmt", ["turtle", "json-ld"])
def test_semantic_import_reuses_governed_pipeline_and_roundtrips(semantic_import_case, fmt):
    result = semantic_import_case.import_document(fmt, kind="instances")
    assert result.failed_count == 0
    assert semantic_import_case.exported_graph_isomorphic_to_expected()
    assert semantic_import_case.provenance_keeps_source_iris_graph_and_blank_scope()
    assert semantic_import_case.schema_import_creates_draft_not_active_release(fmt)
    assert semantic_import_case.unauthorized_import_status(fmt) in {403, 404}
    assert semantic_import_case.remote_context_or_invalid_document_status(fmt) == 422
```

- [ ] **Step 2: 跑红。** `pytest tests/integration/test_ingestion_pipeline.py tests/integration/test_ingestion_projection.py -q`；预期缺编排服务或候选隔离/幂等/水位断言失败。
- [ ] **Step 3: 最小实现。** 预检保存不可变原件、staging、逐行错误及固定 bundle，不改正式事实；apply 校验输入摘要、映射基线、当前权限和适用审批后 enqueue。每行原始→候选→SHACL/权属/裁决→S03 受控写入按独立可恢复事务推进；一行失败不留下该行半个实体/幽灵关系。新对象用 `validate_object` 预检再 `register_object`，既有对象用 `validate_facts`/`FactPatch`；每条 `AssertionInput.resource_id` 必须与目标一致，几何用 `GeometryInput`。源 delete 只撤对应 support，最后一份支持消失才失效当前事实并触发 S04。命令/身份绑定/裁决依据/断言/outbox 同 UoW，投影失败绝不重跑源端副作用。
- [ ] **Step 4: 跑绿。** 重跑上述命令与 I01–I03；证明 SHACL 错误能定位源行/字段/Shape，错误端点 PG/RDF 均无已采纳边；API受理不伪装投影及推理完成。更新 OpenAPI。
- [ ] **Step 5: 小提交。** `git add apps/api/src/lzo/ingestion apps/api/src/lzo/worker/handlers.py apps/api/alembic/versions/0204_ingestion_runs_rows.py apps/api/tests/integration/test_ingestion_pipeline.py apps/api/tests/integration/test_ingestion_projection.py apps/api/tests/integration/conftest_ingestion.py contracts/openapi.json`；`git commit -m "feat: apply governed imports through semantic facts and durable outbox"`。

### I05：规则影响报告、审批绑定与可恢复重算/回滚

**Files:**
- Create: `apps/api/src/lzo/ingestion/rules.py`, `apps/api/src/lzo/ingestion/recalculation.py`。
- Modify: `apps/api/src/lzo/ingestion/schemas.py`, `apps/api/src/lzo/ingestion/models.py`, `apps/api/src/lzo/ingestion/router.py`, `apps/api/src/lzo/ingestion/worker.py`。
- Create: `apps/api/alembic/versions/0205_ingestion_rule_impacts.py`；规则版本、生效指针、影响输入、重算逐项结果和补偿关联。
- Test: `apps/api/tests/integration/test_ingestion_rule_versions.py`, `apps/api/tests/integration/test_ingestion_recalculation.py`；扩充本模块 fixture。

**Interfaces — consumes:** F06 ApprovalService；F05 enqueue/checkpoint/set_job_state；F02 只追加历史；I02 `RulingService`；I04 `process_row`；S01/S04 投影/推理水位。不得以回滚名义更新历史表。

**Interfaces — produces:**

```python
RuleVersion(id: UUID, revision: int, kind: Literal["mapping", "alignment", "authority", "ruling"],
            scope: ResourceScope, parent_id: UUID | None, rules: tuple[RuleExpr, ...],
            mapping_profile: MappingProfile | None, alignment_rules: tuple[AlignmentRule, ...],
            authority_rules: tuple[AuthorityRule, ...], ruling_policies: tuple[RulingPolicyRule, ...],
            status: Literal["draft", "approved", "published", "deprecated"], digest: str)
ImpactSelection(resource_ids: tuple[UUID, ...], source_ids: tuple[UUID, ...], scope: ResourceScope)
ImpactCommand(target_bundle_id: UUID, selection: ImpactSelection, rollback_of: UUID | None)
FactDifference(resource_id: UUID, predicate_iri: str, before_assertion_ids: tuple[UUID, ...],
               after_assertion_ids: tuple[UUID, ...], expected_revision: int, requires_review: bool)
RuleImpact(id: UUID, revision: int, target_bundle: ExecutionBundle, selection: ImpactSelection,
           input_snapshots: tuple[BlobRef, ...], input_watermark: int, baseline: dict[str, int],
           differences: tuple[FactDifference, ...], preserved_ruling_ids: tuple[UUID, ...],
           reevaluate_ruling_ids: tuple[UUID, ...], new_conflict_ids: tuple[UUID, ...],
           change_digest: str, approval_id: UUID, rollback_of: UUID | None)
PublishRuleCommand(rule_version_id: UUID, expected_revision: int, impact_id: UUID, approval_id: UUID)
RecalculationCommand(impact_id: UUID, expected_revision: int, approval_id: UUID)
RecalculationItem(id: UUID, job_id: UUID, resource_id: UUID, expected_revision: int,
                  state: Literal["pending", "applied", "needs_review", "failed", "compensated"],
                  before_revision: RevisionRef, after_revision: RevisionRef | None,
                  compensation_revision: RevisionRef | None, error_code: str | None)
RecalculationView(id: UUID, job: JobRef, impact_id: UUID, bundle_id: UUID,
                  domain_state: Literal["queued", "running", "paused", "partial_failure", "completed", "rolling_back", "rolled_back"],
                  pending_count: int, applied_count: int, review_count: int, failed_count: int,
                  compensated_count: int, projection_watermark: int | None, inference_watermark: int | None)
RuleService.create_version(uow: UnitOfWork, actor: ActorContext, version: RuleVersion) -> RuleVersion
RuleService.impact(actor: ActorContext, command: ImpactCommand) -> RuleImpact
RuleService.publish(uow: UnitOfWork, actor: ActorContext, command: PublishRuleCommand) -> RevisionRef
RecalculationService.start(uow: UnitOfWork, actor: ActorContext, command: RecalculationCommand) -> JobRef
RecalculationService.run_batch(actor: ActorContext, job_id: UUID, lease_token: UUID) -> tuple[RecalculationItem, ...]
RecalculationService.rollback_preview(actor: ActorContext, job_id: UUID) -> RuleImpact
RecalculationService.rollback(uow: UnitOfWork, actor: ActorContext, command: RecalculationCommand) -> JobRef
```

HTTP：`GET/POST /rules/versions` → `listRuleVersions/createRuleVersion`，`Page[RuleVersion]/RuleVersion`；`GET /rules/versions/{id}` → `getRuleVersion`，`RuleVersion`；`POST /rules/impact` → `previewRuleImpact`，201 `RuleImpact`；`GET /rules/impact/{id}` → `getRuleImpact`，`RuleImpact`；`POST /rules/{id}/publish` → `publishRules`，`RevisionRef`。`POST /recalculations` → `startRecalculation`，202 `JobRef`；`GET /recalculations/{id}` → `getRecalculation`，`RecalculationView`；`POST /recalculations/{id}/rollback-preview` → `previewRollback`，`RuleImpact`；`POST /recalculations/{id}/rollback` → `rollbackRecalculation`，202 `JobRef`。大影响报告走 F05 作业、持久差异和游标分页；控制操作严格复用 F05 `POST /jobs/{id}/pause|resume|retry`，领域汇总 `domain_state` 不扩展公共 JobState。

`RecalculationView.id` 等于其 `job.id`，避免前端收到 JobRef 后无法查领域记录；入队前的“待审核/已批准”由 RuleImpact.approval_id 对应 F06 批准记录展示，不制造没有 JobRef 的假任务。

异步 preview/impact 完成时写 F05 `JobView.result_ref`，分别链接本计划 `GET /imports/previews/{id}` 与 `GET /rules/impact/{id}`；结果读取仍按当前权限，错误或未完成不能设置成功报告链接。

- [ ] **Step 1: 写失败测试。** `recalculation_case` 保存原始快照、已采纳值、旧规则、人工裁决及两名审核角色；`approve()` 只调用 F06 `submit/decide`，不直接改状态。

```python
def test_impact_is_readonly_and_resume_does_not_repeat_committed_items(recalculation_case):
    case = recalculation_case
    before = case.current_revision_ids()
    impact = case.rules.impact(case.actor, case.impact_command)
    assert case.current_revision_ids() == before
    assert impact.preserved_ruling_ids and impact.reevaluate_ruling_ids
    job = case.start_approved(impact)
    case.run_one_batch_and_pause(job)
    committed = case.current_revision_ids()
    case.resume_and_drain(job)
    assert case.each_input_has_at_most_one_committed_result(job)
    assert case.revisions_still_exist(committed)

def test_revocation_or_new_revision_blocks_old_approval_and_compensation(recalculation_case):
    case = recalculation_case
    job = case.start_approved(case.rules.impact(case.actor, case.impact_command))
    case.revoke_execution_grant()
    with pytest.raises(Forbidden):
        case.recalculator.run_batch(case.actor, job.id, case.lease_token)
    assert case.no_new_effective_revisions(job)
    assert case.rollback_after_concurrent_edit().new_conflict_ids
```

加 `test_bulk_approval_cannot_accept_new_material_conflict`：新实质冲突留 needs_review，不被重算采纳；`test_running_batch_pins_old_bundle_and_cannot_overwrite_newer_revision`；`test_rollback_appends_compensation_and_preserves_original_evidence`。重算输入只从归档快照读取，关闭外部源后仍可完成试算。

- [ ] **Step 2: 跑红。** `pytest tests/integration/test_ingestion_rule_versions.py tests/integration/test_ingestion_recalculation.py -q`；预期未实现影响/重算或权限、并发、历史断言失败。
- [ ] **Step 3: 最小实现。** 影响报告绑定快照、水位、相关裁决依赖和 expected revisions，交 F06 判风险并审核。发布切换规则指针仅影响新批次；已运行批次固定 bundle。每批提交前重验当前授权、批准摘要与基线；失效就停留可复核状态。暂停停止领取新批次，正在事务按边界结束；重试只处理失败项。回滚生成独立 impact/批准/补偿修订，后续有效更新形成冲突而非强覆写；切回生效规则与补偿某次结果是两个命令。部分失败/待审核记录在逐项表，不伪装 JobState 新增 `partially_succeeded`。
- [ ] **Step 4: 跑绿。** 重跑上述命令及 I02/I04；断点续做、权限撤销、旧批准、关闭来源试算、并发回滚、原历史不可改均 PASS；更新 OpenAPI。
- [ ] **Step 5: 小提交。** `git add apps/api/src/lzo/ingestion apps/api/alembic/versions/0205_ingestion_rule_impacts.py apps/api/tests/integration/test_ingestion_rule_versions.py apps/api/tests/integration/test_ingestion_recalculation.py apps/api/tests/integration/conftest_ingestion.py contracts/openapi.json`；`git commit -m "feat: govern rule changes and resumable recalculation compensation"`。

### I06：跨来源并发、候选隔离及端到端证据责任链

**Files:**
- Create: `apps/api/tests/integration/test_ingestion_multisource.py`, `apps/api/tests/integration/test_ingestion_provenance_chain.py`。
- Create: `apps/api/src/lzo/ingestion/provenance.py`, `docs/operations/source-onboarding.md`, `docs/verification/ingestion-contracts.md`。
- Modify: `apps/api/src/lzo/ingestion/router.py`, `apps/api/src/lzo/ingestion/schemas.py` 及先前测试暴露缺陷的本模块实现；不复制 S/F 的历史或授权代码。

**Interfaces — consumes:** I01–I05；S03 断言支持与当前状态；S04 多重证明撤回；S05 授权查询；F03 当前授权历史查询；F02 不可变修订链。测试仍使用实际 PG、MySQL、内部 PG 与 Fuseki。

**Interfaces — produces:**

```python
ProvenanceEntry(assertion_id: UUID, support_id: UUID, source_key: SourceKey,
                snapshot: BlobRef, locator: str, raw_value: JsonObject, normalized_value: RdfTerm,
                mapping_version_id: UUID, bundle_id: UUID, ruling_ref: RevisionRef | None,
                evidence_refs: tuple[RevisionRef, ...], effective: bool,
                valid_from: datetime | None, valid_to: datetime | None, recorded_at: datetime)
ProvenanceView(entity_id: UUID, assertion_id: UUID, entries: tuple[ProvenanceEntry, ...],
               revision_refs: tuple[RevisionRef, ...], projection_watermark: int | None,
               inference_watermark: int | None)
ProvenanceService.read(actor: ActorContext, entity_id: UUID, assertion_id: UUID) -> ProvenanceView
```

HTTP：`GET /sources/provenance?entity_id=&assertion_id=` → `getSourceProvenance`，200 `ProvenanceView`。按当前授权过滤整条责任链及附件读取；无权支持不泄露来源名、摘要、数量或定位信息。完整历史分页复用 F03 `/history`，不增加任意历史时点全图重建功能。

- [ ] **Step 1: 写失败集成测试。** `multisource_case` 以 `ThreadPoolExecutor` 同时运行真实 PG/MySQL/CSV 导入相同已确认业务键；每线程独立 UoW/actor request ID；`provenance_case` 提供同一实体的两份有效支持、一份冲突候选及已审批裁决。

```python
def test_concurrent_equivalent_sources_create_one_identity_and_three_supports(multisource_case):
    results = multisource_case.import_concurrently(["postgresql", "mysql", "csv"])
    assert all(r.failed_count == 0 for r in results)
    assert multisource_case.entity_count() == 1
    assert multisource_case.active_support_count() == 3
    multisource_case.replay_all()
    assert multisource_case.entity_count() == 1
    assert multisource_case.active_support_count() == 3

def test_withdraw_one_source_preserves_other_support_and_auditable_original(provenance_case):
    case = provenance_case
    case.delete_first_source_and_drain()
    view = case.provenance.read(case.actor, case.entity_id, case.assertion_id)
    assert sum(e.effective for e in view.entries) == 1
    assert case.accepted_fact_present() and case.derived_fact_present()
    assert case.candidate_absent_from_default_query()
    assert all(e.snapshot.sha256 and e.locator and e.bundle_id for e in view.entries)
    case.delete_last_source_and_drain()
    assert not case.accepted_fact_present() and not case.derived_fact_present()
    assert case.all_original_history_exists()
```

补 `test_synthetic_real_name_collision_requires_confirmed_alignment`、`test_shared_device_relationship_grants_no_ingestion_permission`、`test_history_and_evidence_follow_current_grants_after_revocation`。手动改现行事实后旧批次重放不得覆盖；未知身份、单位、CRS 的错误仍完整留在源记录和逐项结果。

- [ ] **Step 2: 跑红。** `pytest tests/integration/test_ingestion_multisource.py tests/integration/test_ingestion_provenance_chain.py -q`；预期责任链端点缺失或跨源/撤回/授权断言失败，不能删掉失败用例规避并发问题。
- [ ] **Step 3: 最小实现。** `provenance.py` 聚合已授权的原始→规范化→候选→裁决→采纳→推理引用；完整原件通过 F04 授权下载。修复暴露的唯一约束竞争、幂等键、支持撤回和执行前权限缺陷，保持各职责归属；S01/S04 缺陷提交相应任务修复，不在 ingestion 写第二套投影/推理引擎。文档说明同源替换、新源绑定、删除屏障、定时身份、失败隔离及复核操作。
- [ ] **Step 4: 跑绿。** `pytest tests/unit/test_ingestion_files.py tests/unit/test_ingestion_mapping.py tests/integration -k ingestion -q`；避免依赖宿主 shell 展开通配符，CI 启用同一真实来源 profile。报告实际参数数、PASS/FAIL/not run、逐层计数、水位及原始测试输出；将 T03/T04/T05/T10/T17 与本模块覆盖的 T12/T18/T19/T20 对应到具体用例，完整三场景 E2E 留 D01。
- [ ] **Step 5: 小提交。** `git add apps/api/src/lzo/ingestion apps/api/tests/integration/test_ingestion_multisource.py apps/api/tests/integration/test_ingestion_provenance_chain.py apps/api/tests/integration/conftest_ingestion.py docs/operations/source-onboarding.md docs/verification/ingestion-contracts.md contracts/openapi.json`；`git commit -m "test: verify multisource consistency and immutable evidence lineage"`。

## 计划自检与交接

- 六任务依次覆盖：文件/证据、映射身份裁决、真实来源同步、统一应用投影、规则重算回滚、跨源责任链；不存在只写接口不验收实际数据库的替代门。
- Review Focus 五类输入均有所属任务断言；S03 只定义一套语义 DTO，F02–F06 只定义一套事务/任务/审批/历史接口。
- 新代码与测试尚未创建，上述红绿命令尚未运行；本文是实施计划，不是实现或测试通过报告。
- 本分计划随总计划共同审阅；先完成整体计划确认再按既定执行方式实施，不单独启动产品编码或安装依赖。
