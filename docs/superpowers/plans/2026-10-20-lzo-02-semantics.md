# 本体与语义内核 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现可发布、可动态扩展、可校验、可解释撤回、按当前权限查询的真实 RDF/OWL 内核。

**Architecture:** PG 的不可变对象、断言及发布修订通过 outbox 投影至 Fuseki/TDB2，当前图与历史权威记录分离。Semantica Adapter 调用经源码核查的实际 SHACL 能力；有限规则推理另建可追溯证明，不将 OWL 生成或 SDK 返回成功当作推理结果。公开 SPARQL 只在查询前构建的授权 RDF 数据集内运行。

**Tech Stack:** 复用总计划的 Python 3.12、FastAPI/Pydantic v2、PostgreSQL/PostGIS、Fuseki/TDB2、RDFLib/pySHACL、Semantica、pytest 和 Compose 测试镜像，精确版本由 F01 锁定。

**Spec:** [已确认设计](../specs/2026-10-20-lzo-platform-p0-p1-design.md)；[总计划及公共契约](2026-10-20-lzo-p0-p1-implementation.md)。本文仅覆盖 S01–S06，接入、前端、完整离线验收见相邻分计划。

## Global Constraints

- 农田、水塘、果园并行建模和验证；暂不纳入畜牧场景。
- RDF 存储采用 Apache Jena Fuseki/TDB2，集成 Semantica 语义能力。
- 首版用明确标记的合成数据，但使用真实连接器、数据库和持久化流程验证。
- 原始、候选、已采纳与派生事实分开；来源删除不能物理删除本体实体。
- 设备只有一个全局身份和一个主管理归属；服务范围、管理归属与访问权限独立。
- 高风险操作不能自审；规则、Schema、证明、断言与裁决保留版本和完整历史。
- 首版不要求完整 OWL 2 DL；推理不能直接触发高风险设备控制。
- 历史按当前权限读取；首版不要求全平台任意历史时点回放。
- 标准验收环境为 8 核 CPU、32GB 内存、SSD，10 并发、1 万实例、10 万条基础关系。
- 不硬编码场景、来源或设备类型上限；预算、并发、分页和批量大小是可调整运维参数。
- 全部 HTTP 路由使用 `/api/v1`；幂等键、If-Match、错误体与状态码严格复用总计划。
- 本计划不安装依赖、不初始化工程、不执行软件测试；下列代码均是实施时写入的测试规范。

## Review Focus

1. SDK 静默退回内存或命名图丢失：S01 检查独立 HTTP 读取与容器重启后的持久性。
2. JSON-LD 远程上下文、OWL imports 或 SPARQL SERVICE 发起外联：S02/S05 拒绝未打包依赖，禁用查询外联。
3. 候选关系的 domain/range 给端点补类型后通过校验：S03 在写前使用独立、已确认类型闭包验证。
4. 多依据和环形成自支撑，Schema 撤回后旧结论残留：S04 从有效基础支持计算最小不动点，保存历史证明。
5. 聚合、路径、证明、导出和共享撤权暴露受限节点：S05 在执行前裁剪授权数据集，并重验授权版本。

## 文件职责与跨计划契约

`apps/api/src/lzo/contracts.py` 的 ActorContext、ResourceScope、RevisionRef、BlobRef、ExecutionBundle、JobRef、Page、JsonObject、UnitOfWork 均由 F02 定义，本计划不复制公共类型。

| 路径 | 职责 |
|---|---|
| `apps/api/src/lzo/semantic/rdf.py`、`files.py`、`fuseki.py`、`projection.py` | RDF 项、语义文件解析、真实 HTTP 存储与幂等投影 |
| `apps/api/src/lzo/semantic/semantica_adapter.py`、`validation.py` | 外部调用隔离、支持矩阵与 SHACL 报告 |
| `apps/api/src/lzo/ontology/{schemas,models,drafts,releases,router}.py` | 草稿、定义、兼容分析、发布与表单元数据 |
| `apps/api/src/lzo/semantic/{schemas,facts,geometry,router}.py` | 断言、支持、实体/关系与几何命令 |
| `apps/api/src/lzo/semantic/{inference,proofs,inference_jobs}.py` | 有限推理、证明依赖、撤回与任务水位 |
| `apps/api/src/lzo/semantic/{authorized_dataset,query,exports}.py` | 授权数据集、只读 SPARQL、邻域与语义导出 |
| `packages/ontology/{core,agriculture,devices,shapes,rules}/` | 有版本的通用基础包及三场景配置 |

统一消费 F02 `append_revision(uow, actor, resource_id, expected_revision, payload, event_type) -> RevisionRef`，F03 `require_permission(actor, action, resource) -> None`、`authorized_resource_ids(actor, action) -> Iterator[UUID]`，F04 `ArtifactStore.put/open`，F05 `enqueue(uow, actor, kind, payload, bundle_id) -> JobRef`。

F06 `ApprovalService.require_executable(actor, approval_id, change_digest, baseline: dict[str,int]) -> ApprovalRef` 验证提交摘要和最新基线；ApprovalRef 字段为 `id/submitter_id/reviewer_id/risk/change_digest/baseline`。历史追加使用 F02，历史列表与对比使用 F03 HistoryService；S 系列只注册其领域记录，不另建历史基础设施。

所有命令在调用者 UoW 内工作，不隐式 commit。HTTP 服务层负责 commit；I04 可以把对象、事实、几何和检查点放入同一事务。F02 outbox 是唯一投影入口；I04 不另写 RDF，也不重复 enqueue 投影。投影成功后由 S04 安排推理，I 系列只读取水位。

平台 ledger/projection/inference 全局水位均为 `int`，来自 F02 追加事件的全局序号；来源快照 token、摘要与连接器游标仍可为字符串，不能与平台水位混用。

迁移使用 S0101–S0199 号段；实施整合时只追加至现有 Alembic head，保持单链，不能并行发布分叉或改写已执行迁移。D01 检查空库、升级路径和唯一 head。

命令均在仓库根运行；pytest 路径相对于 api-test 的 `/workspace/apps/api`。以下完整 Compose 命令由 F01 提供真实 PG、Fuseki 与测试网络，不能换成 SQLite 或内存 RDF 替代持久化验收。

## 官方能力核查记录

计划阶段核查日期为 2026-10-20；下列 `main` 仅用于定位源码，S01 必须对 F01 选定 commit/tag 重新读取并保存摘要，不能把浮动分支当锁版本。

- [Semantica SHACL 源码](https://github.com/semantica-agi/semantica/blob/main/semantica/ontology/ontology_validator.py)：`run_shacl_validation(data_graph_str, shacl_str, data_graph_format="turtle", shacl_format="turtle")` 实际调用 pySHACL，关闭输入推理；返回 conforms、violations、warnings、infos。主链使用此明确函数并做正反例契约测试。
- 同一源码的 `OntologyValidator.validate(ontology)` 含占位/模拟检查，不能作为本项目一致性或 DL 推理的通过证据；文档中同名 facade 不作为无测试的替代。
- [Semantica JenaStore 源码](https://github.com/semantica-agi/semantica/blob/main/semantica/triplet_store/jena_store.py)：`JenaStore(endpoint=...)`、`add_triplets(..., graph=...)` 和 `execute_sparql(...)` 可供契约核查，但初始化失败可能使用本地 Dataset，Triplet 的字面值处理也不足以保证 datatype/language 往返。本项目核心存储采用标准 HTTP 适配器，不能依赖 SDK 的隐式回退。
- [Semantica OWLGenerator 源码](https://github.com/semantica-agi/semantica/blob/main/semantica/ontology/owl_generator.py)：`OWLGenerator().generate_owl(ontology, format="turtle")` 是序列化入口，不是带证明的实例推理器；本项目导入保真使用 RDFLib Dataset。
- [Fuseki 服务配置](https://jena.apache.org/documentation/fuseki2/fuseki-config-endpoint)、[GSP](https://jena.apache.org/documentation/fuseki2/soh.html)用于锁定 query/update/data 端点；[TDB2 管理](https://jena.apache.org/documentation/tdb2/tdb2_admin.html)用于 D03 的受控备份，不能复制运行中任意数据库文件。
- [SHACL 标准](https://www.w3.org/TR/shacl/)与 [SPARQL 标准](https://www.w3.org/TR/sparql11-query/)约束本项目支持清单；预算或白名单拒绝必须返回可定位错误，不能悄悄改变查询含义。

---

### S01：真实 Fuseki/TDB2 与 Semantica 能力契约

**Files:**
- Create: `apps/api/src/lzo/semantic/{rdf,files,fuseki,projection,semantica_adapter,validation}.py`。
- Create: `apps/api/src/lzo/worker/handlers.py`、`docs/verification/semantic-capabilities.md`。
- Create: `apps/api/alembic/versions/S0101_semantic_projection.py`（投影水位、失败状态及推理失效 fence；不保存任意大文件）。
- Modify: `infra/fuseki/config.ttl`、`infra/compose/compose.yaml`、`apps/api/pyproject.toml` 与 F01 的锁文件，仅增加经验证依赖。
- Test: `apps/api/tests/integration/test_semantic_adapters.py`、`apps/api/tests/integration/test_projection.py`；fixtures 在 `apps/api/tests/integration/conftest.py`。

**Interfaces:**
- Consumes: F01 `compose_endpoints`、F02 `uow`、F04 `artifacts`、F05 Worker 处理入口；内部端点不向浏览器或局域网公开写端口。
- `rdf.py` 定义 `RdfTerm(kind: Literal["iri","literal"], value: str, datatype_iri: str|None, language: str|None)`、`Triple(subject_iri: str, predicate_iri: str, object: RdfTerm)`；语言和 datatype 互斥，非绝对 IRI、非法字面值拒绝。
- `FusekiRepository.replace_graph(graph_iri: str, triples: tuple[Triple,...]) -> str` 返回规范内容摘要；`select(query: str, timeout_ms: int) -> JsonObject` 仅内部可信调用；`read_graph(graph_iri: str) -> tuple[Triple,...]`；`health() -> bool` 必须探测真实服务器。
- `ProjectionReceipt(resource_id: UUID, revision: int, state: Literal["synced","superseded","failed"], digest: str)`；`project_revision(revision: RevisionRef) -> ProjectionReceipt` 位于 `projection.py`，读取 PG 权威修订，不接受调用者上传三元组。
- `mark_inference_dirty(uow: UnitOfWork, revision: RevisionRef, kind: Literal["facts","schema","rules"]) -> None` 位于 projection.py，与基础/Schema/规则修订同事务更新 fence；S02/S03/I05 复用，S04 完成一致输入后推进已解决水位。尚无依赖索引时可保守屏蔽全部当前派生结论，不能放行不确定的旧支持。
- `handlers.py` 使用 F05 `register_handler("rdf.project", handler)`；handler 消费 JobContext，failed receipt 必须使任务失败而非成功完成；synced 后安排 S04 推理。Schema 中的空白节点保留标准 RDF 原件，投影中按产物摘要确定性 skolem 化，不能跨文档错误合并匿名 Shape。
- `files.py` 定义 `ParsedSemanticDocument(dataset: rdflib.Dataset, artifact: BlobRef, format: str)`；`parse_semantic_document(blob: BlobRef, format: Literal["turtle","json-ld"]) -> ParsedSemanticDocument` 通过 F04 读取原件，保留命名图与每个原件的空白节点作用域。解析前拒绝外部 context/import 及未打包依赖，不允许解析器联网；Schema 和实例都复用，S02 无需依赖 I 系列。
- `ValidationIssue(focus_iri: str, path_iri: str|None, shape_iri: str|None, constraint_iri: str, severity: Literal["Violation","Warning","Info"], message: str, value: RdfTerm|None)`。
- `ValidationReport(conforms: bool, violations: tuple[ValidationIssue,...], report_artifact: BlobRef|None, bundle_id: UUID)`；`SemanticaAdapter.validate(data_ttl: str, shapes_ttl: str, bundle: ExecutionBundle) -> ValidationReport`。错误是异常，不得转成 conforms=True。
- 测试 fixtures：`rdf_repo: FusekiRepository`、`semantica_adapter: SemanticaAdapter`、`bundle: ExecutionBundle`；初始化指向真实 Compose 服务，禁网测试只封锁外网。

- [ ] **Step 1: 写入适配器与投影失败测试。**

```python
def test_remote_typed_literal_and_named_graph(rdf_repo):
    from lzo.semantic.rdf import RdfTerm, Triple
    term = RdfTerm(kind="literal", value="12.50", datatype_iri="http://www.w3.org/2001/XMLSchema#decimal", language=None)
    triple = Triple(subject_iri="urn:lzo:test:a", predicate_iri="urn:lzo:test:area", object=term)
    rdf_repo.replace_graph("urn:lzo:test:accepted", (triple,))
    assert rdf_repo.read_graph("urn:lzo:test:accepted") == (triple,)
    assert rdf_repo.read_graph("urn:lzo:test:candidate") == ()

def test_semantica_is_real_validator(semantica_adapter, bundle):
    shapes = '@prefix sh: <http://www.w3.org/ns/shacl#>. <urn:shape> a sh:NodeShape; sh:targetNode <urn:a>; sh:property [sh:path <urn:name>; sh:minCount 1].'
    report = semantica_adapter.validate('<urn:a> a <urn:Device>.', shapes, bundle)
    assert report.conforms is False
    assert report.violations[0].path_iri == "urn:name"
    assert report.violations[0].shape_iri is not None
```

补同文件测试：中文语言标签往返；故障端点必须失败而非回退内存；合法输入 conforms=True；缺包显式失败；parse_semantic_document 对含两个 @graph 的 JSON-LD 断言恢复两个不同图上下文，对两个文件相同 bnode 标签断言不混同，对远程 context/import 断言拒绝且无 HTTP 请求。`test_projection.py` 通过 F02 append_revision 构造新、旧与重复事件，断言新水位不被旧修订覆盖、相同事件重放不增加支持记录、失败可重试且 pending 不等于 synced。

- [ ] **Step 2: 运行红测。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_semantic_adapters.py tests/integration/test_projection.py -q`

Expected: 缺少上述适配器或投影行为而 FAIL；服务不可达属于环境失败，不能充当红测逻辑证据。

- [ ] **Step 3: 实现协议适配、分图投影与核查记录。**

以 HTTP GSP/SPARQL Update 写真实 TDB2，分别保存 Schema、Shape、candidate、accepted、provenance、derived 图；原始大文件仅保存 BlobRef。每个修订先写不可变分图，单次内部 Update 更新有效指针和 RDF 水位，PG 失败时安全重放；资源锁与版本条件避免乱序覆盖。基础修订或 Schema 生效时登记 inference_dirty/期望输入水位，受影响当前结论标记 stale；S03 命令事务先记录失效水位，S01 投影确认后推进 RDF 水位，避免等待异步投影期间旧证明仍被当作有效。所有字面值由 RDFLib 编码，禁止拼接用户字符串。记录 SDK 源码 SHA、实际调用、支持/不支持项、失败语义；选定版本缺函数时调整锁定版本并重跑契约，不静默省略 Semantica。

- [ ] **Step 4: 运行绿测与重启验证。**

重复 Step 2 命令，Expected: 全 PASS；再运行 `docker compose -f infra/compose/compose.yaml restart fuseki`，随后 `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_semantic_adapters.py -q --check-persisted-rdf`。S01 在 conftest 注册该参数：只读检查前次专用图并断言 term 和图边界不变，不重新 seed 后伪称持久。

- [ ] **Step 5: 小提交。**

Run: `git add apps/api/src/lzo/semantic apps/api/alembic/versions/S0101_semantic_projection.py apps/api/tests/integration/test_semantic_adapters.py apps/api/tests/integration/test_projection.py apps/api/tests/integration/conftest.py infra/fuseki/config.ttl infra/compose/compose.yaml apps/api/src/lzo/worker/handlers.py apps/api/pyproject.toml docs/verification/semantic-capabilities.md`；将实际变更的 F01 锁文件逐个加入，`git commit -m "feat: add verified Fuseki and Semantica adapters"`。

### S02：OWL/SHACL 定义、草稿、动态元数据与受控发布

**Files:**
- Create: `apps/api/src/lzo/ontology/{schemas,models,drafts,releases,router}.py`、`apps/api/src/lzo/semantic/semantic_profile.py`。
- Create: `apps/api/alembic/versions/S0102_ontology_releases.py`、`docs/verification/semantic-profile.md`。
- Modify: `apps/api/src/lzo/main.py`、`apps/api/src/lzo/worker/handlers.py`。
- Test: `apps/api/tests/integration/test_schema_releases.py`、`apps/api/tests/unit/test_semantic_profile.py`。

**Interfaces:**
- Consumes: S01 repository/validation，F02 revisions，F03 schema.edit/schema.review/schema.publish，F04 artifacts，F05 enqueue，F06 ApprovalService。
- `SchemaDocument(classes: tuple[ClassDef,...], properties: tuple[PropertyDef,...], relations: tuple[RelationDef,...], shapes: str, ui_metadata: JsonObject, dependencies: tuple[BlobRef,...])`。
- `ClassDef(iri: str, label: str, description: str, parents: tuple[str,...], deprecated: bool)`。
- `PropertyDef(iri: str, label: str, datatype_iri: str, unit: str|None, owner_policy_iri: str, enum_values: tuple[RdfTerm,...], superproperties: tuple[str,...])`。
- `RelationDef(iri: str, label: str, domain_iris: tuple[str,...], range_iris: tuple[str,...], superproperties: tuple[str,...], inverse_iri: str|None, symmetric: bool, transitive: bool)`。
- `SchemaDraftView(id: UUID, revision: int, base_release_id: UUID|None, document: SchemaDocument, state: str)`；`SchemaReleaseView(id: UUID, revision: int, version: str, state: str, document: SchemaDocument, artifacts: tuple[BlobRef,...])`。
- `ImpactReport(digest: str, baseline: dict[str,int], affected_ids: tuple[UUID,...], violations: tuple[ValidationIssue,...], form_diff: JsonObject, permission_diff: JsonObject, migration_required: bool)`；`SchemaDiffView(base_release_id: UUID, target_release_id: UUID, changes: JsonObject, impact: ImpactReport)`。
- `FormField(iri: str, label: str, datatype_iri: str|None, reference_classes: tuple[str,...], required: bool, min_count: int, max_count: int|None, unit: str|None, enum_values: tuple[RdfTerm,...], read_only: bool, widget: str)`；`FormSchemaView(class_iri: str, release_id: UUID|None, fields: tuple[FormField,...], inherited_from: dict[str,tuple[str,...]])`。
- `InheritanceView(class_iri: str, ancestors: tuple[str,...], fields: tuple[FormField,...], conflicts: tuple[str,...])`；`preview_release(actor, draft_id: UUID) -> ImpactReport`；`publish_release(uow, actor, draft_id: UUID, approval_id: UUID, expected_revision: int) -> JobRef`。
- 请求 DTO：`CreateDraftRequest(base_release_id: UUID|None, document: SchemaDocument)`、`UpdateDraftRequest(document: SchemaDocument)`、`PublishReleaseRequest(draft_id: UUID, approval_id: UUID)`；版本条件仅由 If-Match 映射到 expected_revision，不接受两个冲突版本来源。submit 请求为 `SubmitDraftRequest(impact_digest: str)`，服务端重验报告并调用 F06 submit。
- `SemanticDraftImport(blob: BlobRef, format: Literal["turtle","json-ld"], kind: Literal["schema","shapes"], base_release_id: UUID|None, target_draft_id: UUID|None, expected_revision: int)`；`import_semantic_draft(uow: UnitOfWork, actor: ActorContext, request: SemanticDraftImport) -> SchemaDraftView` 位于 drafts.py，复用 S01 parser，保留原件、图上下文索引与支持报告。新草稿 target_draft_id=None/expected_revision=0，已有草稿须验证版本，不自动发布。
- `SchemaAxiom(iri: str, triple: Triple, release_id: UUID)` 为每条支持的公理生成按不可变产物摘要与规范 triple 确定的索引，供 S04 证明引用；不把 import 图名或相同标签当公理身份。
- S02 的 models.py 与 S0102 迁移负责持久化不可变 ExecutionBundle：`create_execution_bundle(uow: UnitOfWork, actor: ActorContext, schema_release_id: UUID, mapping_version_id: UUID|None, authority_version_id: UUID, ruling_policy_version_id: UUID, inference_version_id: UUID, shapes_digest: str) -> ExecutionBundle`；`get_execution_bundle(actor: ActorContext, bundle_id: UUID) -> ExecutionBundle` 位于 releases.py。
- 首次发布同时以 F02 通用修订登记默认权属、裁决和有限推理配置版本：平台自有属性允许授权人工录入，外部值先走候选及确定规则/审核，不自动认领身份或覆盖冲突；六类推理仍须相应公理显式启用。bundle 引用这些修订 ID，不依赖尚未实现的 I05 专用表；I05 后续发布规则版本时创建新 bundle，不修改旧快照。

HTTP：GET/POST `/schema/drafts` → `Page[SchemaDraftView]`/SchemaDraftView，operationId `listSchemaDrafts/createSchemaDraft`；GET/PATCH `/{id}` → SchemaDraftView，`getSchemaDraft/updateSchemaDraft`。GET `/{id}/inheritance?class_iri=` → InheritanceView，`getDraftInheritance`；GET `/{id}/form-schema?class_iri=` → FormSchemaView，`getDraftFormSchema`；POST `/{id}/validate` → ImpactReport，`validateSchemaDraft`；POST `/{id}/submit` → ApprovalRef，`submitSchemaDraft`。GET `/schema/releases`、GET `/{id}`、GET `/{id}/diff?base_release_id=`、GET `/{id}/form-schema?class_iri=` 分别 `listSchemaReleases/getSchemaRelease/diffSchemaRelease/getReleasedFormSchema`；POST `/schema/releases` 接受 draft_id/approval_id、返回 202 JobRef，`publishSchemaRelease`。已有路由的 `/{id}` 均相对该行首个资源前缀。

POST `/schema/drafts/imports` 接受 SemanticDraftImport → SchemaDraftView，operationId `importSemanticDraft`；静态 imports 路由优先于 `/{id}`。I04 只负责上传/批次包装与调用此服务；Schema 解析和发布不反向依赖接入模块。

- [ ] **Step 1: 写入发布治理与语义配置失败测试。**

```python
def test_schema_self_review_and_stale_approval(api_client, schema_case):
    draft = schema_case.new_subclass("AmmoniaSensor", parent="Sensor")
    self_review = schema_case.approve(draft, reviewer=schema_case.editor)
    assert self_review.status_code == 403
    approval = schema_case.approve(draft, reviewer=schema_case.reviewer).json()
    schema_case.change_label(draft, "氨氮传感器")
    result = api_client.post("/api/v1/schema/releases", json={"draft_id": draft["id"], "approval_id": approval["id"]}, headers=schema_case.write_headers(draft))
    assert result.status_code == 409
    assert result.json()["code"] == "APPROVAL_STALE"

def test_import_remote_dependencies_rejected(profile):
    result = profile.inspect_jsonld({"@context": "https://outside.invalid/context"})
    assert result.allowed is False
    assert "REMOTE_CONTEXT_FORBIDDEN" in result.codes
```

`schema_case` 在 `test_schema_releases.py` 同目录 fixture 定义为真实 API fixture 工厂：editor/reviewer 为 F03 两个独立授权身份；new_subclass/approve/change_label/write_headers 只构建上述 HTTP 请求与幂等/版本头，不绕过数据库。`profile` 为 SemanticProfile；`inspect_jsonld(document: JsonObject) -> ProfileInspection(allowed: bool, codes: tuple[str,...])` 位于 semantic_profile.py。

新增断言：继承循环与冲突基数拒绝；不支持 OWL 公理报告原件位置；JSON-LD named graph 不漏项；IRI 冲突、缺依赖拒绝；安装故障不切有效版本；恢复重试后只切换一次；新增结构必须独立审批；纯标签变更可走 F06 low 风险；不兼容 Shape 必须有迁移计划；并发草稿 If-Match 冲突；Schema 影响报告不返回无权对象名称或总数；仅 F/S 迁移存在时首次发布仍能持久化完整默认 bundle，重启读回所有版本 ID 不变。

- [ ] **Step 2: 运行红测。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_schema_releases.py tests/unit/test_semantic_profile.py -q`；Expected: 发布/配置支持缺失导致 FAIL。

- [ ] **Step 3: 实现不可变发布状态机和元数据生成。**

实现 draft→submitted→approved→installing→published/failed/deprecated，批准摘要包含依赖、影响及基线；执行前重验 F06，不把 PG 提交视为 RDF 已安装。先校验/安装不可变 OWL、Shape、UI 产物并读回验证，再切有效快照；中断可恢复。Core 支持类型、基数、范围、枚举、模式和端点，登记高级 Shape 支持情况。草稿保留修订供 U02 恢复/撤销；多重继承合并字段且显式报告冲突。S02 直接依赖 S01 的 RDFLib Dataset parser 执行 Schema/Shape 导入与配置检查，I04 后续复用而非反向供给解析；不联网解析 imports/context，不静默丢弃语义。影响报告对外只含有权对象；完整跨域报告由明确全局治理权限读取，无权影响不能通过名称、计数或差异泄漏。

- [ ] **Step 4: 运行绿测。**

重复 Step 2 命令，Expected: PASS；确认发布后 form-schema 新字段可读、旧版本产物仍可读取、历史当前权限过滤仍有效。生成 OpenAPI 使用总计划约定脚本，由 U01 生成客户端。

- [ ] **Step 5: 小提交。**

Run: `git add apps/api/src/lzo/ontology apps/api/src/lzo/semantic/semantic_profile.py apps/api/alembic/versions/S0102_ontology_releases.py apps/api/tests/integration/test_schema_releases.py apps/api/tests/unit/test_semantic_profile.py apps/api/src/lzo/main.py apps/api/src/lzo/worker/handlers.py docs/verification/semantic-profile.md`；`git commit -m "feat: add governed ontology drafts and releases"`。

### S03：动态实例、关系断言、几何与严格 SHACL

**Files:**
- Create: `apps/api/src/lzo/semantic/{schemas,models,facts,geometry,ownership,router}.py`、`apps/api/alembic/versions/S0103_objects_assertions_geometry.py`。
- Modify: `apps/api/src/lzo/main.py`、`apps/api/tests/integration/conftest.py`。
- Test: `apps/api/tests/integration/test_dynamic_objects.py`、`apps/api/tests/integration/test_fact_supports.py`、`apps/api/tests/integration/test_geometry.py`。

**Interfaces:**
- Consumes: S02 发布定义/FormSchemaView，S01 ValidationReport/RdfTerm，F02/F03/F04/F05 的同一事务契约；accepted 写入验证 decision_ref 对应已授权裁决或确定规则评估，不能仅相信请求的 state。
- `DataNature = Literal["synthetic","test","imported_unverified","verified"]`。
- `SourceSupport(support_id: UUID, source_id: UUID|None, record_key: str|None, source_version: str|None, snapshot: BlobRef|None, raw_value: JsonObject, mapping_version_id: UUID|None, evidence_refs: tuple[RevisionRef,...], observed_at: datetime|None)`；recorded_at 由平台生成。
- `AssertionInput(assertion_id: UUID, resource_id: UUID, predicate_iri: str, object: RdfTerm, state: Literal["candidate","accepted"], data_nature: DataNature, valid_from: datetime, valid_to: datetime|None, support: SourceSupport)`；resource_id 必须与所属 FactPatch/CreateObjectInput 一致，IRI 对象必须解析为已登记实体或支持的词汇。
- `CreateObjectInput(resource_id: UUID, class_iris: tuple[str,...], display_name: str, owner_scope_id: UUID, related_scope_ids: tuple[UUID,...], state: Literal["draft","active"], data_nature: DataNature, assertions: tuple[AssertionInput,...], decision_ref: RevisionRef|None)`。
- `FactPatch(resource_id: UUID, expected_revision: int, additions: tuple[AssertionInput,...], withdraw_support_ids: tuple[UUID,...], decision_ref: RevisionRef|None)`；`WriteReceipt(revision: RevisionRef, assertion_ids: tuple[UUID,...], projection_state: Literal["pending","synced","failed"])`。
- `register_object(uow, actor, create: CreateObjectInput, bundle: ExecutionBundle) -> WriteReceipt`；`apply_fact_patch(uow, actor, patch: FactPatch, bundle: ExecutionBundle) -> WriteReceipt`；`validate_facts(actor, facts: tuple[AssertionInput,...], bundle: ExecutionBundle) -> ValidationReport` 均在 facts.py，后者用于已存在主体。
- `validate_object(actor, create: CreateObjectInput, bundle: ExecutionBundle) -> ValidationReport` 直接用 create 的类和初始断言构建待校验图供新实体预检；register_object 内部复用，无需先提交空实体。I04 不自行写对象表。新实体 expected_revision=0；来源 accepted 值须具有效 decision_ref，手工自有字段以经授权人工录入修订为依据。
- `GeometryInput(resource_id: UUID, expected_revision: int, geojson: JsonObject, source_crs: str, source_support: SourceSupport, decision_ref: RevisionRef|None)`；`write_geometry(uow, actor, geometry: GeometryInput, bundle: ExecutionBundle) -> RevisionRef`；只改几何入口不改变主管理归属。
- `ObjectView(id: UUID, iri: str, revision: int, class_iris: tuple[str,...], display_name: str, state: str, data_nature: DataNature, owner_scope_id: UUID, related_scope_ids: tuple[UUID,...], properties: dict[str,tuple[RdfTerm,...]], projection_state: str, bundle_id: UUID)`。
- `AssertionView(id: UUID, resource_id: UUID, predicate_iri: str, object: RdfTerm, state: str, data_nature: DataNature, valid_from: datetime, valid_to: datetime|None, supports: tuple[SourceSupport,...], decision_ref: RevisionRef|None, revision: RevisionRef, bundle_id: UUID)`；`LinkView(id: UUID, source_id: UUID, predicate_iri: str, target_id: UUID, assertion: AssertionView)`。
- `GeoFeatureCollection(type: Literal["FeatureCollection"], features: list[JsonObject], projection_watermark: int, next_cursor: str|None)`；每个 Feature 的 properties 必含 object_id/geometry_revision/data_nature/source_crs，且按当前权限过滤。
- HTTP 请求 DTO：`CreateObjectRequest(create: CreateObjectInput, bundle_id: UUID)`、`UpdateObjectRequest(patch: FactPatch, bundle_id: UUID)`；服务端验证 bundle_id 为允许生效快照，不能自行指定旧版绕过约束。
- `CreateLinkRequest(source_id: UUID, target_id: UUID, predicate_iri: str, assertion_id: UUID, state: Literal["candidate","accepted"], data_nature: DataNature, valid_from: datetime, valid_to: datetime|None, support: SourceSupport, decision_ref: RevisionRef|None, bundle_id: UUID)` 转换成 FactPatch，主体 expected_revision 来自 If-Match；`WithdrawLinkRequest(support_ids: tuple[UUID,...], decision_ref: RevisionRef|None, bundle_id: UUID)` 仅撤回指定支持。
- `DeactivateObjectRequest(reason: str, approval_id: UUID|None)`；`GeometryWriteRequest(geometry: GeometryInput, bundle_id: UUID)`；`ValidateFactsRequest(bundle_id: UUID, facts: tuple[AssertionInput,...]|None, create: CreateObjectInput|None)` 要求 facts/create 二选一。正文与 If-Match 若重复携带版本必须一致，否则 422。
- `ownership.py` 仅定义后续能力端口：`TransferGrantChange(action: Literal["retain","revoke","add"], grant_id: UUID|None, principal_id: UUID, operation: str, scope_id: UUID)`；`TransferProposal(resource_id: UUID, expected_revision: int, old_owner_scope_id: UUID, new_owner_scope_id: UUID, grant_delta: tuple[TransferGrantChange,...], impact_digest: str, baseline: dict[str,int])`。
- `OwnershipTransferPort(Protocol).preview(actor: ActorContext, resource_id: UUID, new_owner_scope_id: UUID, expected_revision: int) -> TransferProposal`、`.execute(uow: UnitOfWork, actor: ActorContext, proposal: TransferProposal, approval_id: UUID) -> WriteReceipt`；契约要求 UUID/IRI/来源绑定/历史不变，授权保留/撤销/新增显式列入 grant_delta。首版只保留类型协议和约束文档，不暴露假可用端点、不实现完整后续转移流程。

HTTP：GET/POST `/objects` → Page[ObjectView]/WriteReceipt（`listObjects/createObject`）；GET/PATCH `/objects/{id}` → ObjectView/WriteReceipt（`getObject/updateObject`），PATCH 接受 FactPatch；POST `/{id}/deactivate` → WriteReceipt（`deactivateObject`），GET `/{id}/form-schema` → FormSchemaView（`getObjectFormSchema`），GET `/{id}/assertions` → Page[AssertionView]（`listObjectAssertions`）。GET/POST `/links` → Page[LinkView]/WriteReceipt（`listLinks/createLink`）；POST `/links/{id}/withdraw` 接受 support_ids/decision_ref → WriteReceipt（`withdrawLink`）。POST `/validation` 接受 facts/bundle_id 或 create/bundle_id 的互斥请求 → ValidationReport（`validateFacts`）。GET `/geometry?bbox=&scope_id=&cursor=` → GeoFeatureCollection（`listGeometry`）；GET/PUT `/geometry/{object_id}` → GeoFeatureCollection/RevisionRef（`getObjectGeometry/updateObjectGeometry`）。候选采纳/拒绝统一 I02 `/conflicts/{id}/rulings`，不另建第二套审核。

- [ ] **Step 1: 写入元数据扩展、端点和多支持失败测试。**

```python
def test_domain_range_cannot_repair_endpoint(object_case):
    response = object_case.link("Sensor", "monitors", "Shipment", state="accepted")
    assert response.status_code == 422
    assert response.json()["code"] == "LINK_ENDPOINT_INCOMPATIBLE"
    assert object_case.accepted_links() == []
    assert "Pond" not in object_case.types("Shipment")

def test_withdraw_one_source_preserves_other(fact_case):
    fact_case.accept_same_value(sources=("gis-a", "gis-b"))
    fact_case.withdraw_support("gis-a")
    assert fact_case.current_value() == "12.50"
    assert fact_case.valid_supports() == ("gis-b",)
    fact_case.withdraw_support("gis-b")
    assert fact_case.current_value() is None
    assert fact_case.object_still_exists() is True
```

这些 fixture 工厂定义在 conftest，仅封装真实服务/UoW，object_case 按 S02 发布 Sensor/Pond/Shipment；fact_case 以 SourceSupport 保存两个真实来源支持，方法读取权威表及真实 RDF。另断言：新子类字段自动出现；候选不进入默认事实；创建 active 必填严格而 draft 可不完整；错误来源拥有字段 PATCH=403；幂等重放稳定 UUID；多区域同设备只保留一个主管理归属；几何缺 CRS=422、空/非法几何隔离、正确坐标/单位记录修订；source 删除不删平台备注；非 verified 人工采纳后仍非 verified。

- [ ] **Step 2: 运行红测。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_dynamic_objects.py tests/integration/test_fact_supports.py tests/integration/test_geometry.py -q`；Expected: 缺命令/断言策略导致 FAIL。

- [ ] **Step 3: 实现唯一身份、事实支持及空间事务。**

主体、支持和断言分别登记；相同结论可有多个有效支持。UUID 与稳定 IRI 不随名称、来源或服务区域变化；related_scope_ids 不等于授权。语义端点从已确认类型及合法子类闭包检查，不能借待写关系推导类型。事务结束前验证完整有效事实，active 不完整不得采纳；候选单独登记报告。规范化时间/单位不改变真实性；原始值、证据版本和裁决关联不丢失。几何保存 PostGIS 与历史修订，RDF 只投影稳定引用和必要语义；范围/归属变更走 F06，禁止普通字段写入扩权。

- [ ] **Step 4: 运行绿测。**

重复 Step 2 命令，Expected: PASS；确认 register_object/apply_fact_patch/write_geometry 同事务失败时三者全部回滚且无投影事件，成功时返回 pending 与可追踪修订。source 权属/裁决策略由 I02/I04 提供，本任务只验证依据契约和合法写入，不复制接入治理规则引擎。

- [ ] **Step 5: 小提交。**

Run: `git add apps/api/src/lzo/semantic/schemas.py apps/api/src/lzo/semantic/models.py apps/api/src/lzo/semantic/facts.py apps/api/src/lzo/semantic/geometry.py apps/api/src/lzo/semantic/ownership.py apps/api/src/lzo/semantic/router.py apps/api/alembic/versions/S0103_objects_assertions_geometry.py apps/api/src/lzo/main.py apps/api/tests/integration/conftest.py apps/api/tests/integration/test_dynamic_objects.py apps/api/tests/integration/test_fact_supports.py apps/api/tests/integration/test_geometry.py`；`git commit -m "feat: add dynamic objects facts and governed geometry"`。

### S04：有限推理、证明依赖与安全撤回

**Files:**
- Create: `apps/api/src/lzo/semantic/{inference,proofs,inference_jobs}.py`、`apps/api/alembic/versions/S0104_inference_proofs.py`。
- Modify: `apps/api/src/lzo/semantic/router.py`、`apps/api/src/lzo/semantic/projection.py`、`apps/api/src/lzo/worker/handlers.py`。
- Test: `apps/api/tests/unit/test_inference_closure.py`、`apps/api/tests/integration/test_inference_retraction.py`。

**Interfaces:**
- Consumes: S01 投影水位，S02 不可变 ExecutionBundle，S03 有效 accepted 断言/支持，F02 历史及 F05 任务取消/检查点。
- `InferenceInput(bundle: ExecutionBundle, input_watermark: int, assertions: tuple[AssertionView,...], schema_triples: tuple[Triple,...])`；`InferenceBudget(max_triples: int, max_proofs: int, timeout_ms: int)` 是运维预算。
- `ProofNode(id: UUID, conclusion: Triple, rule_iri: str, premise_assertion_ids: tuple[UUID,...], premise_proof_ids: tuple[UUID,...], schema_axiom_iris: tuple[str,...], bundle_id: UUID, active: bool)`。
- `InferenceResult(conclusions: tuple[Triple,...], proofs: tuple[ProofNode,...], input_watermark: int, bundle_id: UUID, complete: bool)`；`derive(inputs: InferenceInput, budget: InferenceBudget) -> InferenceResult`。
- 证明持久化时为结论的规范 triple 生成稳定派生 Assertion UUID/IRI，并记录每个运行的 active 状态；同结论多证明共享该身份。真实性沿依据展示，不因被推理而升级为 verified，混合依据明确标记。
- `schedule_inference(uow, actor, revision: RevisionRef, bundle: ExecutionBundle) -> JobRef` 在投影水位确认事务入队，按 resource/revision/bundle 去重；`recompute_inference(actor, bundle: ExecutionBundle, input_watermark: int) -> JobRef`。
- `ProofView(assertion_id: UUID, conclusion: Triple, alternatives: tuple[ProofNode,...], bundle_id: UUID, input_watermark: int, truncated: bool)`；GET `/graph/proofs/{assertion_id}` → ProofView，operationId `getAssertionProof`。本任务先实现内部服务，公开路由在 S05 授权证明过滤完成后才装配，分页展开不能返回受限依据。

- [ ] **Step 1: 写入六类规则、循环和多依据撤回测试。**

```python
def test_multiple_proofs_and_no_self_support(inference_case):
    inference_case.accept_edges(("a", "b"), ("b", "c"), ("a", "c"), ("c", "a"))
    first = inference_case.recompute()
    assert first.complete is True
    assert ("a", "c") in inference_case.current_edges()
    inference_case.withdraw_edge("a", "c")
    inference_case.recompute()
    assert ("a", "c") in inference_case.current_edges()
    inference_case.withdraw_all_base_edges()
    inference_case.recompute()
    assert inference_case.current_edges() == set()
    assert inference_case.historical_proofs(first.bundle_id)

def test_schema_axiom_removed_retracts_conclusion(inference_case):
    inference_case.accept_subclass_instance("AmmoniaSensor", "Sensor")
    assert inference_case.has_type("Sensor") is True
    inference_case.publish_without_subclass_axiom()
    inference_case.recompute()
    assert inference_case.has_type("Sensor") is False
    assert inference_case.historical_has_type("Sensor") is True

def test_failed_budget_does_not_expose_invalid_old_conclusion(inference_case):
    inference_case.accept_subclass_instance("AmmoniaSensor", "Sensor")
    assert inference_case.has_type("Sensor") is True
    inference_case.withdraw_last_type_support()
    inference_case.recompute_with_budget(max_triples=0)
    assert inference_case.last_job_status() == "failed"
    assert inference_case.current_public_query_has_type("Sensor") is False
    assert inference_case.historical_has_type("Sensor") is True
```

`inference_case` 定义在 test_inference_retraction.py，由真实 S02/S03 API 与 Worker 工厂构成；current_edges 只返回指定传递谓词，historical_proofs 通过 F03 当前授权读取。单元参数集覆盖多级子类、类型传播、子属性、inverseOf、显式对称、显式传递的正反例；未声明关系不推断。集成补 candidate/test 状态不误作 verified，单一/多来源支持撤回，属性公理变更、重复任务、取消、超预算和旧水位任务不可覆盖新结果。

- [ ] **Step 2: 运行红测。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/unit/test_inference_closure.py tests/integration/test_inference_retraction.py -q`；Expected: 缺证明或撤回错误导致 FAIL。

- [ ] **Step 3: 实现最小不动点与不可变证明运行。**

按六类明确规则推导，证明记录直接前提、规则和 Schema 公理。首版撤回对受影响连通分量从仍有效基础支持重新计算最小不动点；不能仅做引用计数，因为环会自支撑。子类/子属性/Schema 规则变化可退回全闭包重算，在基准内测量成本。相同结论合并为一个当前结论、保留不同有效证明；证明有环时有限引用并提供截断标记，不枚举无限路径。预算耗尽时旧闭包只作为历史保存，已受基础或公理变化影响的结论保持 stale 并由 S05 屏蔽；任务显式失败/待恢复，不发布不完整闭包，也不让失去依据的旧结果继续生效。成功运行按水位切换当前 derived 图，旧运行/证明追加留存，撤回不删历史。

- [ ] **Step 4: 运行绿测。**

重复 Step 2 命令，Expected: PASS；比较同一固定输入的两次结论集合和证明指纹相同，确认基础、派生、历史证明分离，取消后可幂等恢复且无设备调用。

- [ ] **Step 5: 小提交。**

Run: `git add apps/api/src/lzo/semantic/inference.py apps/api/src/lzo/semantic/proofs.py apps/api/src/lzo/semantic/inference_jobs.py apps/api/src/lzo/semantic/projection.py apps/api/src/lzo/semantic/router.py apps/api/src/lzo/worker/handlers.py apps/api/alembic/versions/S0104_inference_proofs.py apps/api/tests/unit/test_inference_closure.py apps/api/tests/integration/test_inference_retraction.py`；`git commit -m "feat: add proof tracked inference and safe retraction"`。

### S05：查询前授权数据集、SPARQL、图查询与语义导出

**Files:**
- Create: `apps/api/src/lzo/semantic/{authorized_dataset,query,exports}.py`。
- Modify: `apps/api/src/lzo/semantic/router.py`、`apps/api/src/lzo/semantic/proofs.py`、`apps/api/src/lzo/worker/handlers.py`。
- Test: `apps/api/tests/integration/test_authorized_queries.py`、`apps/api/tests/integration/test_semantic_export.py`。

**Interfaces:**
- Consumes: F03 authorized_resource_ids/current authorization version，S01 真实 RDF 读取，S03 断言可见性，S04 完整证明依赖，F04 私有 ArtifactStore，F05 Jobs。
- `QueryRequest(query: str, mode: Literal["current","review"], timeout_ms: int, max_rows: int)`；review 额外要求 candidate.read，不能访问原始服务命名图。
- `AuthorizedDataset(dataset: rdflib.Dataset, authorization_version: int, input_watermark: int, bundle_id: UUID)`；`build_authorized_dataset(actor, mode: str) -> AuthorizedDataset` 在 authorized_dataset.py，dataset 仅内部进程使用，不作为 JSON 返回。
- `QueryResult(kind: Literal["select","ask","graph"], variables: tuple[str,...], rows: tuple[dict[str,RdfTerm],...], boolean: bool|None, graph: tuple[Triple,...], input_watermark: int, authorization_version: int, truncated: bool)`。
- `execute_query(actor, request: QueryRequest) -> QueryResult`；`GraphNeighborhood(nodes: tuple[ObjectView,...], edges: tuple[LinkView,...], next_cursor: str|None, input_watermark: int)`；`neighborhood(actor, resource_id: UUID, hops: int, cursor: str|None) -> GraphNeighborhood`。
- `ExportRequest(resource_ids: tuple[UUID,...], format: Literal["turtle","json-ld"], layout: Literal["single-graph","dataset"], graph_role: Literal["schema","accepted","candidate","derived","provenance"]|None, include_provenance: bool, include_inferred: bool)`；`start_export(uow, actor, request: ExportRequest) -> JobRef`。single-graph 必须指定 graph_role，不能混入其他事实层；dataset 保留图上下文，产物访问由 F04 route 当前授权重验，不能返回公开路径。
- Turtle 的 single-graph 产物为单个 `.ttl`；dataset 产物为 ZIP，含 `manifest.json` 与逐图 `.ttl`，清单记录逻辑图角色、图 IRI、文件、摘要和空白节点作用域，不能静默 flatten。JSON-LD dataset 使用保留图名的 `@graph` 表达；无权图不进入包。
- POST `/query/sparql` → QueryResult（`executeSparql`）；GET `/graph/neighborhood?resource_id=&hops=&cursor=` → GraphNeighborhood（`getGraphNeighborhood`）；POST `/query/exports` → 202 JobRef（`createSemanticExport`）；GET `/graph/proofs/{assertion_id}` 复用 S04（`getAssertionProof`）。公开不提供 SPARQL UPDATE。

- [ ] **Step 1: 写入聚合、路径、受限证明和撤权失败测试。**

```python
def test_hidden_nodes_absent_before_aggregation(query_case):
    query_case.seed_visible_a_hidden_b_visible_c()
    count = query_case.sparql("SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s a <urn:lzo:Device> }")
    assert count.rows[0]["n"].value == "2"
    path = query_case.sparql("ASK { <urn:lzo:a> <urn:lzo:linkedTo>+ <urn:lzo:c> }")
    assert path.boolean is False
    assert query_case.proof_through_hidden_b().status_code == 404

def test_service_forbidden_and_revoke_immediate(query_case):
    blocked = query_case.request("SELECT * WHERE { SERVICE <https://outside.invalid/sparql> { ?s ?p ?o } }")
    assert blocked.status_code == 422
    assert blocked.json()["code"] == "SPARQL_OPERATION_FORBIDDEN"
    export_job = query_case.start_export()
    query_case.revoke_shared_grant()
    assert query_case.object_request().status_code == 404
    assert query_case.run_export(export_job).status == "failed"
```

`query_case` 在 test_authorized_queries.py 定义：三个实体/关系在真实 Fuseki，actor 仅能读 a/c，b 的名称/坐标/关系均受限；一个已有缓存与未执行导出任务用于撤权。补测 ASK/COUNT/OPTIONAL/MINUS/子查询/GRAPH ?g/属性路径/CONSTRUCT/DESCRIBE/猜测IRI、多个证明仅一条完全可见时的结论、candidate 模式拒绝、FROM/FROM NAMED/更新/扩展函数拒绝、超时/大结果、导出和证明无受限名字或计数、旧会话撤权以及原始溯源值权限。

- [ ] **Step 2: 运行红测。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_authorized_queries.py tests/integration/test_semantic_export.py -q`；Expected: 无查询前隔离/旧缓存泄漏导致 FAIL。

- [ ] **Step 3: 实现隔离数据集与有界执行。**

先按 F03 授权对象分批读取真实 Fuseki 当前图，保留主体和资源对象均可见的事实；词汇对象仅允许已发布可见词表。溯源、候选及证明各检查所指资源/证据权限。派生结论必须至少有一条全部可见的有效基础支持与 Schema 公理证明；仅看结论端点不够。还要检查当前 PG 有效支持、stale 标记及基础/Schema/推理一致水位；失去依据或水位不一致的受影响结论不进入查询、解释或导出，即使上次推理图仍存在、重算失败或尚未投影。构建独立 RDFLib Dataset，默认图仅授权 accepted+可见 derived，命名图仅逻辑 schema/accepted/derived 及经授权 candidate/provenance，绝不包含原始存储图。

随后才在该 Dataset 执行解析后的 SELECT/ASK/CONSTRUCT/DESCRIBE；拒绝 SERVICE、Update、FROM/FROM NAMED、未支持扩展函数及远程解引用，以 AST 检查而非正则字符串匹配。查询进程禁网，独立进程设置超时/内存/结果/路径预算并可取消；不能仅在返回后隐藏节点。缓存键含 actor/授权版本/输入水位/bundle/mode，返回或流式分块前重验当前权限；变化则丢弃重建。邻域、路径、聚合、证明和导出复用相同授权数据集。导出 Turtle/JSON-LD 保留允许的 datatype/language/断言与来源；无权部分显式报告范围缩减，不伪称全量备份。

- [ ] **Step 4: 运行绿测。**

重复 Step 2 命令，Expected: PASS；单图 Turtle 往返比较 triple，Turtle dataset 必须依 manifest 重建上下文后比较 quad，JSON-LD dataset 比较 quad（含图名），空白节点用数据集同构/确定性规范化比较，不按字符串排序冒充语义等价。加入两个事实层含相同 SPO 的导出测试，断言恢复后仍为两个图、没有合并。确认任何公开参数都不能指定内部 Fuseki 端点/任意图，公开 API 不能代理内网请求。

- [ ] **Step 5: 小提交。**

Run: `git add apps/api/src/lzo/semantic/authorized_dataset.py apps/api/src/lzo/semantic/query.py apps/api/src/lzo/semantic/exports.py apps/api/src/lzo/semantic/router.py apps/api/src/lzo/semantic/proofs.py apps/api/src/lzo/worker/handlers.py apps/api/tests/integration/test_authorized_queries.py apps/api/tests/integration/test_semantic_export.py`；`git commit -m "feat: enforce authorization before semantic queries"`。

### S06：三场景基础包与内核跨层验证

**Files:**
- Create: `packages/ontology/core/core.ttl`、`packages/ontology/agriculture/agriculture.ttl`、`packages/ontology/devices/devices.ttl`。
- Create: `packages/ontology/shapes/core.shacl.ttl`、`packages/ontology/rules/finite-profile.json`、`packages/ontology/releases/synthetic-v1.json`。
- Create: `apps/api/src/lzo/ontology/bootstrap.py`、`apps/api/tests/integration/test_semantic_kernel.py`、`apps/api/tests/fixtures/semantic/{field,pond,orchard}.json`。
- Create: `docs/verification/semantic-kernel.md`；Modify: `apps/api/src/lzo/worker/handlers.py`。

**Interfaces:**
- Consumes: S01–S05 与 F02/F03/F06，不直接写 PG/RDF 绕过审批；D01 合成规模生成器复用这些版本化包和 fixture，不重新定义核心词汇。
- `BootstrapManifest(version: str, namespace: str, package_artifacts: tuple[BlobRef,...], inference_profile: str, data_nature: Literal["synthetic"], digest: str)`；`bootstrap_packages(uow, actor, manifest: BootstrapManifest, approval_id: UUID) -> JobRef` 复用 S02 发布状态机，幂等安装。
- 基础词汇含 AgriculturalEntity、Farm、AgriculturalZone、Field、Pond、Orchard、CropConcept、CultivationCycle、Device、Sensor、Camera、WaterGate、Pump、IrrigationEquipment、AgriculturalMachine、Drone、Organization、Source、Evidence、Assertion。分类词汇对齐 SKOS，观测/来源对齐适用的 SOSA/SSN 与 PROV-O，不承诺完整上层标准。
- 关系分别声明 managedBy/serves/locatedIn/partOf/monitors/controls/mountedOn；只为确有语义的属性显式启用 inverse/symmetric/transitive，不按中文名称猜语义。主管理归属通过受控引用和基数约束，服务多值不授予权限。
- JSON fixtures 字段使用 CreateObjectInput/FactPatch/GeometryInput 公共领域 DTO，均明确 synthetic，稳定 seed=20261020；三个场景各含空间对象、作物引用、至少两种关联设备和一个跨区域共享设备引用。
- 不新增公开 bootstrap 后门；执行身份需 schema.publish，并有 F06 与包摘要绑定批准。初始化账号及首批 reviewer 通过 F03 部署流程建立，不能因安装绕过独立审核。

- [ ] **Step 1: 写入三场景内核完整链路失败测试。**

```python
@pytest.mark.parametrize("scene", ["field", "pond", "orchard"])
def test_scene_semantic_kernel(scene, kernel_case):
    result = kernel_case.load_scene(scene)
    assert result.data_nature == "synthetic"
    assert result.validation.conforms is True
    assert result.same_object_in_registry_graph_geometry is True
    assert result.inferred_device_type is True
    assert result.proof_has_rule_and_source_versions is True
    assert result.unauthorized_neighbor_count == 0
    kernel_case.withdraw_last_support(result)
    assert kernel_case.current_inference(result) is False
    assert kernel_case.history_contains_original_proof(result) is True

def test_shared_identity_and_dynamic_extension(kernel_case):
    before = kernel_case.shared_device_id()
    kernel_case.add_device_subclass_and_form_field()
    assert kernel_case.form_has_new_field() is True
    assert kernel_case.shared_device_id_in_other_scene() == before
    assert kernel_case.manager_count(before) == 1
```

`kernel_case` 在 test_semantic_kernel.py 定义真实 API 集成工厂；`KernelSceneResult` 为测试本地 dataclass，字段即以上断言字段，必须从对象/SHACL/真实 RDF/证明/几何查询分别计算，不能由载入函数固定 True。withdraw_last_support 走 S03 与真实 Worker；历史读取走 F03。补测发布失败重启可恢复、备份重建当前 RDF 后历史证明不变（完整跨存储恢复归 D03）、模型含七类设备、多重继承冲突被拒绝、无设备控制适配器调用。

- [ ] **Step 2: 运行红测。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/integration/test_semantic_kernel.py -q`；Expected: 基础包/跨层契约缺失导致 FAIL。

- [ ] **Step 3: 实现通用包、合成样例与验证入口。**

按不可变版本清单登记 namespace、依赖与摘要；正式命名空间不跟随部署地址改写。以通用模型支持三场景，作物概念与种植周期分开、设备能力和挂载分开，服务/归属/位置/授权分开。通过真实发布→动态实例→SHACL→投影→推理→授权查询→几何→历史对比链路生成证据。S06 验证内核，完整 PG/MySQL/文件接入由 I06、浏览器/离线/负载由 D 系列完成，报告不混淆覆盖范围。

- [ ] **Step 4: 运行绿测与内核回归。**

Run: `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/unit/test_semantic_profile.py tests/unit/test_inference_closure.py tests/integration/test_semantic_adapters.py tests/integration/test_projection.py tests/integration/test_schema_releases.py tests/integration/test_dynamic_objects.py tests/integration/test_fact_supports.py tests/integration/test_geometry.py tests/integration/test_inference_retraction.py tests/integration/test_authorized_queries.py tests/integration/test_semantic_export.py tests/integration/test_semantic_kernel.py -q`。

Expected: 全 PASS；将实际版本、命令、结果、跳过/失败、RDF 水位与数据性质写入 semantic-kernel.md，未执行项明确记录，不把契约计划当运行报告。

- [ ] **Step 5: 小提交。**

Run: `git add packages/ontology apps/api/src/lzo/ontology/bootstrap.py apps/api/src/lzo/worker/handlers.py apps/api/tests/integration/test_semantic_kernel.py apps/api/tests/fixtures/semantic docs/verification/semantic-kernel.md`；`git commit -m "feat: verify three scene ontology kernel"`。

## 计划自检与交付边界

- S01 覆盖 R07、持久化/失败投影；S02 覆盖 R03/R13/R24/R25、T06/T11/T13 的 Schema 部分；S03 覆盖 R04/R10/R12/R34、T02/T07/T18 的核心命令。
- S04 覆盖 R16–R18、T08/T09；S05 覆盖 R22/R23、T12/T13 的受控查询导出；S06 对 T01/T02/T19/T20 提供语义内核证据。
- R33 裁决重新评估由 I02/I05 主责，S03 保存并校验 decision_ref，S04 不将失效支持继续推理；R35/R36 历史由 F02/F03 主责，语义包/断言/几何/证明均登记修订。
- 五项 Review Focus 均有归属测试；公共类型、S03 接入签名、HTTP operationId 与相邻计划对齐。本文写入测试断言，不写产品实现体。
- 完整性能、1 小时混合负载、离线冷启动和组合恢复仍由 D02–D05 验证；本计划完成不代表完整 P0＋P1 完成。
