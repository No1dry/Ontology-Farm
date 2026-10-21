# 梁子湖农业通用本体平台 P0＋P1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 交付已确认的完整 P0＋P1：三个农业场景共用真实、可治理、可解释、可离线部署的本体平台。

**Architecture:** 模块化 FastAPI API 与 Worker 共用事务、权限和语义服务；PostgreSQL/PostGIS 记录权威修订与空间数据，事务 outbox 将版本化事实可靠投影至 Fuseki/TDB2。React 工作台调用受控 API，Keycloak 提供独立 OIDC，文件、地图、外部来源均通过可替换适配器接入。

**Tech Stack:** Python 3.12、FastAPI/Pydantic v2、SQLAlchemy/Alembic、PostgreSQL/PostGIS、Apache Jena Fuseki/TDB2、RDFLib/pySHACL、Semantica、Keycloak、React/TypeScript/Vite、XYFlow、Cytoscape.js、MapLibre GL JS、Docker Compose、pytest、Vitest、Playwright。

**Spec:** [已确认设计](../specs/2026-10-20-lzo-platform-p0-p1-design.md)。本文及五份分计划共同构成完整实施计划，不得仅执行一份便宣称完整交付。

## Global Constraints

- 范围：完整 P0＋P1，包含本轮讨论确认的多源治理、规则重算、常用推理和离线部署能力。
- 农田、水塘、果园并行建模和验证；暂不纳入畜牧场景。
- RDF 存储采用 Apache Jena Fuseki/TDB2，集成 Semantica 语义能力。
- 首版用明确标记的合成数据，但使用真实连接器、数据库和持久化流程验证。
- 来源删除不能物理删除本体实体；支持失效、停用、历史保留和来源追踪。
- 设备只有一个全局身份和一个主管理归属；服务范围、管理归属与访问权限独立，按区域明确授予查看、使用和特定操作权限。
- 标准验收环境为 8 核 CPU、32GB 内存、SSD，10 并发、1 万实例、10 万条基础关系。
- 形成可复现的自动化测试报告，记录 P50/P95、吞吐量、CPU、内存、错误率；基准不是容量上限。
- 核心功能断开外网仍正常运行，所有关键静态资源和依赖能够内网部署。
- 高风险提交人与审核人不同；历史不可原位改写；原始、候选、采纳、派生事实分开。
- 不启动真实设备控制、不建设独立 P2 Action Engine、不发布公网服务。
- 无法运行的检查记录为未执行，外部依赖不兼容须显式修复；不以 mock 或跳过测试代替验收。

## Review Focus

1. 一批来源读取失败或游标乱序，不得误判大批删除；由 I03 的失败快照及乱序重放测试负责。
2. 区域 A 编辑者兼区域 B 查看者，共享关系和推理中间节点不得扩权；由 F03、S05、U05 的权限负例负责。
3. 任务批准后数据或权限变化，重试不得套用旧批准覆盖新版本；由 F06、I05 的执行前校验测试负责。
4. 循环证明及多个来源支持并存，撤回一个依据不应保留纯循环支持或误撤有效结论；由 S04 的证明撤回测试负责。
5. 离线清缓存、Token 到期及完整恢复后，历史与当前图谱仍可验证；由 D02、D03、D04 的冷启动与恢复测试负责。

## 分计划、顺序与验收门

这是一个相互依赖的平台，按可验证子系统拆成五份计划，保持一个项目和统一接口，不缩减完整交付范围。

| 分计划 | 任务编号 | 可独立验证的交付 | 依赖 |
|---|---|---|---|
| [01 平台基础与治理](2026-10-20-lzo-01-foundation.md) | F01–F06 | 持久化、身份、范围授权、版本、任务、不可变历史 | 无 |
| [02 本体与语义内核](2026-10-20-lzo-02-semantics.md) | S01–S06 | 模型发布、实例与关系、SHACL、可撤回推理、受控 SPARQL | F01–F06 |
| [03 多源接入与重算](2026-10-20-lzo-03-ingestion.md) | I01–I06 | 文件/PG/MySQL 接入、裁决、增量、重算 | F01–F06；I02 起依赖 S01–S03 |
| [04 前端工作台与地图](2026-10-20-lzo-04-workbench.md) | U01–U06 | 真实 API 工作台、治理操作、地图与图谱、动态表单 | F02/F03；各页等待对应 S/I 接口 |
| [05 离线交付与完整验收](2026-10-20-lzo-05-delivery.md) | D01–D05 | 三场景 E2E、离线包、恢复、性能报告和交付文档 | 对应 S/I/U 功能，最终门依赖全部任务 |

执行顺序：F01 → F02 → F03 → F04 → F05 → F06；S01 → S02 → S03 → S04 → S05 → S06。I01 可在 F06 后与 S01 并行，I02–I06按契约依赖推进。U01 在 F03 后可先完成真实登录，其他前端任务等待具体 API；不使用永久模拟成功接口抢跑。D01 的数据规范先确定，完整场景验证在其依赖完成后执行。

内部 P0 门：F 系列、S01–S03/S05、U01–U03 的最小完整链路能新增子类、独立审批发布、创建实例、校验关系、执行 SPARQL、重启后读取。此门不代表全部首版完成。最终 P0＋P1 门要求所有 R01–R36 与 T01–T20 有实际结果。

## 工程目录与单一职责

```text
apps/api/pyproject.toml                 Python 应用和锁定依赖入口
apps/api/src/lzo/main.py               API 装配与路由
apps/api/src/lzo/contracts.py          跨模块公共类型，不含业务服务
apps/api/src/lzo/{auth,governance,...}/ 按业务模块组织 models/schemas/service/router
apps/api/src/lzo/semantic/             RDF、Semantica、SHACL、推理、授权查询
apps/api/src/lzo/ingestion/            连接器、规范记录、映射、身份对齐与同步
apps/api/src/lzo/worker/               持久任务、租约、检查点、outbox 投影
apps/api/alembic/                      只追加的数据库迁移
apps/api/tests/{unit,integration}/     真实语义与数据库验证
apps/web/src/{app,features,shared}/    工作台、按功能页面与通用组件
apps/web/src/api/generated.ts          从 OpenAPI 生成，不手改
packages/ontology/                    首批 OWL/SHACL/规则及版本清单
contracts/openapi.json                 生成的服务契约快照
infra/{compose,fuseki,keycloak,proxy}/ 部署配置及服务构建
scripts/                              启动、验证、备份、离线打包脚本
tests/e2e/                            Playwright 跨模块真实流程
tests/performance/                    固定种子负载与原始指标
docs/operations/                      环境、升级、迁移、备份恢复说明
```

所有测试从真实服务边界选择验证方式：纯规则用单元测试，事务和权限用真实 PG，RDF 用真实 Fuseki；浏览器流程连接实际 API。脚手架、配置和文档随首次使用它们的任务交付，不建立脱离功能的空目录任务。

## 跨计划接口注册表

以下是本项目将实现的接口，不是声称第三方库已有同名函数。第三方 Semantica/Jena 调用由适配器契约测试锁定。

公共类型在 **F02** 的 `apps/api/src/lzo/contracts.py` 定义，所有字段均为显式类型，JSON 时间为 UTC ISO 8601，内部时间采用带时区 datetime：

```python
JsonObject = dict[str, Any]
ActorContext(subject_id: UUID, request_id: str, authorization_version: int)
ResourceScope(resource_id: UUID | None, owner_scope_id: UUID | None,
              related_scope_ids: tuple[UUID, ...], security_class: str)
RevisionRef(resource_id: UUID, revision: int, event_id: UUID)
BlobRef(sha256: str, size_bytes: int, media_type: str, version_id: UUID)
ExecutionBundle(id: UUID, schema_release_id: UUID,
                mapping_version_id: UUID | None, authority_version_id: UUID,
                ruling_policy_version_id: UUID, inference_version_id: UUID,
                shapes_digest: str)
JobRef(id: UUID, status: JobState)
JobState = Literal["queued", "running", "paused", "failed", "succeeded", "cancelled"]
Page[T](items: list[T], next_cursor: str | None)
```

F02 在 `contracts.py` 定义 `UnitOfWork` 协议，在 `db.py` 实现 `PgUnitOfWork`：`__enter__() -> UnitOfWork`、`__exit__(exc_type, exc, tb) -> None`、`commit() -> None`，其 `session: sqlalchemy.orm.Session` 仅服务层访问。正常退出不隐式提交，异常回滚。公共写操作如下：

```python
# governance/revisions.py — F02；新对象 expected_revision=0。
append_revision(uow: UnitOfWork, actor: ActorContext, resource_id: UUID,
                expected_revision: int, payload: JsonObject,
                event_type: str) -> RevisionRef
# auth/permissions.py — F03；不授权时抛统一 Forbidden。
require_permission(actor: ActorContext, action: str,
                   resource: ResourceScope) -> None
# artifacts/store.py — F04；仅内部使用，下载前由路由校验 actor。
ArtifactStore.put(stream: BinaryIO, media_type: str) -> BlobRef
ArtifactStore.open(blob: BlobRef) -> BinaryIO
# worker/service.py — F05；必须与命令/修订在同一个 uow 中写入。
enqueue(uow: UnitOfWork, actor: ActorContext, kind: str,
        payload: JsonObject, bundle_id: UUID | None) -> JobRef
```

详细领域服务由分计划自身的 Interfaces 定义，必须保留上述名称和语义。统一 HTTP 契约：

- `/api/v1` 前缀；身份/权限：`/session`、`/users`、`/groups`、`/grants`。
- 模型/实例：`/schema/drafts`、`/schema/releases`、`/objects`、`/links`、`/validation`。
- 查询：`/query/sparql`、`/graph/neighborhood`、`/geometry`、`/maps/resources`。
- 治理/接入：`/approvals`、`/history`、`/sources`、`/imports`、`/conflicts`、`/rules`、`/recalculations`、`/jobs`、`/artifacts`。
- 所有业务修改携带 `Idempotency-Key`；修改既有资源还必须 `If-Match: "<revision>"`。OIDC 登录/回调遵循认证协议，Cookie 会话修改额外校验 CSRF。冲突响应 409；缺少必需版本条件为 428；校验错误 422；未登录 401；对象不可见时 404；已可见但操作无权 403。错误体 `{code, message, request_id, details}`，code 使用 UPPER_SNAKE_CASE。
- `POST` 创建返回 201；异步受理返回 202 与 JobRef；作业的“完成”另报投影/推理水位，HTTP 受理不等于图谱同步完成。
- 列表游标分页，默认 50/上限 200 为可配置运维值，不是容量上限。授权先于分页、计数、聚合和图遍历。
- OpenAPI 使用稳定 `operationId` 生成 TypeScript；服务端 Pydantic 定义请求与响应。前端不另造 REST 字段名。
- 平台 ledger/input/projection/inference watermark 统一为整数提交序号（F02负责），表示连续完整的已处理范围；来源数据库快照token、来源游标、内容摘要另用各自类型，不混作平台全局水位。

## 执行与验证约定

- 宿主机命令优先 PowerShell；核心测试可在 Compose 的 Linux 测试镜像运行。后续任务采用 `docker compose -f infra/compose/compose.yaml --profile test run --rm api-test pytest tests/... -q`；前端采用相同 Compose 的 `web-test npm run test -- ...`；E2E 使用 `e2e-test npx playwright test ...`。
- F01 创建上述服务与包装脚本。各子计划中的 `pytest tests/...` 表示在 api-test 工作目录 `/workspace/apps/api` 运行；`npm` 表示 web-test 的 `/workspace/apps/web`；E2E 工作目录 `/workspace`。CI 使用同一镜像和命令。
- 根目录 `tests/performance/`、`tests/release/` 的 Python 检查使用 api-test 镜像但明确 `-w /workspace` 工作目录；不误解为 apps/api/tests 下的文件。
- 每个任务按测试失败 → 最小实现 → 有关测试通过 → 代码审阅 → 小提交推进。配置、文档、生成类型不写镜像实现的无意义单元测试。
- 提交仅本任务明确文件，不使用 `git add .` 意外带入原始文档、密钥或数据。计划里的提交消息是执行时使用的建议，当前尚未建立 Git 仓库或提交。
- F01 先验证版本组合再写锁文件：Python 精确补丁版本、Node/npm、Python/npm 依赖、Semantica commit/tag、镜像摘要和 Jena/Keycloak 版本写入 `infra/versions.lock.json`。不能使用浮动 `latest` 作为验收依据。
- Alembic 文件编号分段：F0001–0005、S0101–0199、I0201–0299、U0301–0399；迁移按实际任务整合顺序串行加入单一版本链，`down_revision` 指该次整合分支的现有 head。不并行发布迁移，不改已执行迁移；D01验证唯一head与空库/升级路径。不同任务代码可按依赖并行，不因此生成未经处理的多个数据库head。

## 环境核查记录

计划阶段只读检查发现：`python` 可用，版本 3.12.7；Git 不在当前 PATH，但在 `%LOCALAPPDATA%/Programs/Git/cmd/git.exe` 找到并实测为 2.55.0.windows.5，可按该路径使用，无需重复安装。`node`、`npm`、`docker`、`java` 未在当前 PATH 找到，Node/Docker 常见安装目录也未发现可执行文件。`C:/Windows/System32/wsl.exe` 存在不等于 WSL 发行版、虚拟化或容器引擎已可用。F01 必须实际核查并准备其余工具链。

本机检测到20个逻辑处理器、约31.9GiB内存；尚未核对物理核/虚拟化和磁盘类型，不将其直接认定为8核32GB SSD标准验收环境。探索性结果与标准验收结果分别报告。

不改写系统全局 Python，不将缺少容器误报为软件测试通过。若安装需要用户交互、系统重启或不可自动获取的管理授权，明确指出具体阻塞，继续可独立完成的契约和纯逻辑开发；不得用 SQLite/mock 替代最终持久化验收。

## 需求追踪与自检

完整映射及执行证据在 D05 建立 `docs/verification/requirements-matrix.md`。本计划的自检结果、所有 R/T 对应任务和交付条件在文末记录；该记录证明计划覆盖，不证明软件已经实现。

| 需求 | 实施任务 |
|---|---|
| R01 完整P0＋P1与主要用户 | 全部29任务；D05汇总 |
| R02 三场景不含畜牧 | S06、D01 |
| R03 统一类/属性/关系 | S02、S06、U02 |
| R04 七类设备与动态扩展 | S03、S06、U02、U03 |
| R05 合成数据与真实组件 | F01、S01、I03、D01 |
| R06 PG/MySQL来源与内部库分离 | F01、I03 |
| R07 Jena与Semantica真实集成 | S01、S04、S05 |
| R08 统一接入链 | I01–I04、U04 |
| R09 全量/增量/定时/CDC接口 | I03、I04、F05 |
| R10 删除失效与历史保留 | S03、S04、I03、I06 |
| R11 字段权属与人工冲突处理 | I02、I04、U04 |
| R12 原始/候选/采纳分离与溯源 | F04、S03、I04、I06 |
| R13 前端配置与版本化 | S02、I02、I05、U02、U04 |
| R14 新规则生效与历史受控重算 | F06、I05、U04 |
| R15 快照重算/暂停/重试/回滚 | F05、I05、D03 |
| R16 六类语义规则 | S04 |
| R17 推理解释/版本/撤回 | S04、S05、U05、D03 |
| R18 不做完整DL或真实控制 | S04、U06、D05 |
| R19 本机/Compose/内网与公网预留 | F01、D02、D05 |
| R20 Keycloak/OIDC解耦 | F03、U01 |
| R21 邀请/多角色/多组/多范围 | F03、U06 |
| R22 RBAC/区域/Schema独立权限 | F03、S02、U06 |
| R23 查询/证据/操作不越权 | F03、F04、S05、I06、U05 |
| R24 风险分级与异人审核 | F06、S02、I02、I05、U02、U04 |
| R25 系统判级与多级扩展 | F06 |
| R26 结构化接入/标准语义导入导出 | I01、I04、S01、S02、S05、U04 |
| R27 证据附件/未来抽取接口 | F04、I01、U03、U04 |
| R28 断外网核心/本地静态资源 | F01、F03、U01、U05、D02 |
| R29 在线/MBTiles/PMTiles与自有几何 | S03、U05、D02 |
| R30 实际离线部署/恢复 | D02、D03 |
| R31 指定验收环境与数据规模 | D01、D04 |
| R32 可复现完整性能报告 | D04、D05 |
| R33 裁决按重要变化重评与复核 | I02、I05、U04 |
| R34 唯一设备/单一归属/独立共享 | F03、S03、I02、U06、D01 |
| R35 全类型历史/差异/责任人 | F02、F03、S02–S04、I06、U03、U06、D03 |
| R36 不可变历史/未来转移与时点重建 | F02、F03、S03、D03 |

| 验收 | 负责任务与证据重点 |
|---|---|
| T01 三场景完整链 | D01，S06/I06内核与来源证据 |
| T02 动态扩展 | S02/S03、U02/U03 |
| T03 多源一致 | I02/I06 |
| T04 持续同步故障恢复 | I03/I04、F05 |
| T05 字段与身份冲突 | I02、U04 |
| T06 Schema发布 | S02、F06、U02 |
| T07 SHACL正反例 | S01/S03、U02/U03 |
| T08 六类推理 | S04 |
| T09 撤回/多支持/循环 | S04、I06 |
| T10 受控重算与回滚 | I05、U04 |
| T11 并发与批准过期 | F02/F06、S02、U02/U03 |
| T12 权限全边界 | F03、S05、I06、U05/U06 |
| T13 标准语义文件 | S01/S02/S05、I04、U04 |
| T14 离线冷启动 | D02 |
| T15 底图切换不变对象 | U05、D02 |
| T16 重启与完整恢复 | F01/F05、D03 |
| T17 裁决有效性与复核 | I02/I05、U04 |
| T18 共享身份及授权隔离 | F03/S03、U06、D01 |
| T19 全类型历史对比 | F03、S02–S04、I06、U03/U06、D03 |
| T20 不可变记录与恢复 | F02、D03 |

### 自检与执行交接

- 设计各章节已映射到任务；系统核心不依赖外部云服务，真实源/图存储/身份与离线资源均有实际验证路径。
- 分计划采用精确文件职责、DTO/函数签名、测试断言与运行命令，接口由拥有方定义，消费方不另造实现；OpenAPI前端类型来自后端生成。
- 任务允许工程师实现函数体和选择内部数据结构；不以计划代码片段代替产品实现。五份分计划的测试断言共同覆盖单一设计与原文保留要求，不将里程碑当完整交付。
- 依赖/镜像精确补丁仍需 F01/S01 真正运行兼容测试后锁定；当前只是计划，所有产品功能、测试和部署尚未执行。
- 推荐逐任务由独立实施代理实现、独立审阅后再推进，完成后做全分支审阅；原因是29个任务共享身份、权限、修订及语义接口，错误可能影响数据一致性与可追溯性。
- 用户审阅本计划并选择执行方式后，按选定技能开始实施；此处不再重新询问已确认的业务需求。
