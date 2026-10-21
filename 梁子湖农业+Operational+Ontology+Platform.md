# 梁子湖农业 Operational Ontology Platform
## 可持续演进的本体论与数字孪生基础平台——总体架构、产品设计与实施方案 V1.0

> **项目建议代号**：LZO — Liangzihu Ontology（梁子湖农业本体平台）  
> **文档类型**：总体技术方案 / 产品需求基线 / 第一阶段工程执行蓝图  
> **制定日期**：2026-10-20  
> **目标区域**：湖北省鄂州市梁子湖区；行业：农业、养殖、水利、生产管理与溯源  
> **目标读者**：架构师、前后端工程师、知识图谱工程师、GIS/IoT 工程师、产品经理、Coding Agent  
> **文档定位**：用于搭建一个**能够真正运行、可以长期维护、可以持续迭代**的框架雏形。此文档不是已交付软件、真实现场数据或部署验收报告。  
> **关键约束**：**不使用 `liangzihu-ontology-traceability` 的前端代码、页面结构或视觉设计**。该仓库仅可选作既有农业对象/来源字段目录的参考，不依赖其后端、数据库或业务流程。  
> **重要技术取向**：以 **Semantica 的标准语义技术（RDF、OWL、SHACL、SPARQL、图谱管理和溯源思想）为主要参考**；吸收 Palantir Foundry Ontology 的 Object / Link / Action / Function / Data Source Binding 思想；前端交互参考 Semantica Knowledge Explorer 与 WorldWideView 的优点，但采用**独立、农业领域专属的视觉与交互系统**。

---

# 目录

1. [执行摘要与不可变原则](#1-执行摘要与不可变原则)
2. [目标、边界、成功标准](#2-目标边界成功标准)
3. [参考项目的取舍与技术选型](#3-参考项目的取舍与技术选型)
4. [核心概念与领域建模规则](#4-核心概念与领域建模规则)
5. [总体架构与系统边界](#5-总体架构与系统边界)
6. [本体 Schema 分层与农业初始模型](#6-本体-schema-分层与农业初始模型)
7. [RDF、OWL、SHACL、SPARQL 设计](#7-rdfowlshacl-sparql-设计)
8. [数据库与知识图谱持久化设计](#8-数据库与知识图谱持久化设计)
9. [实体身份、数据接入和同步治理](#9-实体身份数据接入和同步治理)
10. [服务设计与接口契约](#10-服务设计与接口契约)
11. [Action Engine：从知识到可执行业务](#11-action-engine从知识到可执行业务)
12. [前端产品设计与独立视觉规范](#12-前端产品设计与独立视觉规范)
13. [页面级交互流程与前后端联动](#13-页面级交互流程与前后端联动)
14. [GIS、IoT、情报和溯源模块的接入](#14-gisiot情报和溯源模块的接入)
15. [安全、审计、版本、迁移与运维](#15-安全审计版本迁移与运维)
16. [工程目录、环境和本地启动方案](#16-工程目录环境和本地启动方案)
17. [分阶段开发路线与验收标准](#17-分阶段开发路线与验收标准)
18. [端到端示范场景与测试清单](#18-端到端示范场景与测试清单)
19. [风险、技术债和反模式](#19-风险技术债和反模式)
20. [工程团队 / Coding Agent 执行指令](#20-工程团队--coding-agent-执行指令)
21. [长期演进路线、决策记录与参考资料](#21-长期演进路线决策记录与参考资料)

---

# 1. 执行摘要与不可变原则

## 1.1 项目是什么

建设一个**以梁子湖区农业为应用范围、以标准本体语义为知识底座、以现实农业对象为统一业务抽象**的 Operational Ontology 平台，后续为梁子湖数字孪生、IoT 设备管理、农业情报系统与产品溯源提供同一套对象身份、分类、关系和可执行操作入口。

平台首先是**可长期演进的领域基础设施**，不是一次性静态演示，也不是“把数据库表画成知识图谱”的可视化项目。

**一句话定义**：

> 通过 OWL 定义农业世界中的概念，用 RDF 表达实例与关系，用 SHACL 治理数据合法性，用 SPARQL 与统一 API 查询知识，用 Operational Actions 改变业务状态，最后将这些能力以独立设计的本体工作台、地图和业务应用呈现出来。

## 1.2 三种世界必须区分

| 世界 | 存储什么 | 典型示例 | 核心职责 |
|---|---|---|---|
| **概念世界（TBox / Schema）** | 类、子类、属性、关系、规则、命名空间 | `Sensor`、`WaterQualitySensor rdfs:subClassOf Sensor` | 定义**什么是合法的概念和关系** |
| **事实世界（ABox / Instances）** | 真实或合成实体、对象关系、证据及有效时间 | `sensor-001 rdf:type WaterQualitySensor`、`sensor-001 monitors pond-001` | 记录**梁子湖实际有什么** |
| **操作世界（Operational / Kinetic）** | Action Type、参数、权限、规则、执行事件和写回 | `CreateInspectionTask`、`AcknowledgeAlarm`、模拟 `SetGateOpening` | 管理**允许用户/系统做什么** |

三者能相互引用，但不能混为一谈。例如，增加 `WaterQualitySensor` 是修改 Schema，增加 `SENSOR-001` 是创建实例，校准传感器则是执行 Action。

## 1.3 十二条必须坚持的原则

1. **从第一天就是标准语义系统**：OWL/RDFS + RDF + SHACL + SPARQL 是核心能力，不是最后附加的 JSON 导出功能。
2. **Ontology 的本体定义有唯一权威源**：正式 Schema 通过 Git/版本注册表发布；禁止多个服务同时随意改 Schema。
3. **对象身份稳定且跨系统复用**：无论前端名称、设备编号、来源主键如何变化，平台实体 IRI/UUID 不随意改变。
4. **数据权属明确**：地理几何由 GIS 主库维护；真实设备状态由设备服务维护；本体平台不随意篡改上游权威数据。
5. **新增类、属性、关系是数据驱动配置**：无需为每种新对象编写 CRUD 页面，但特殊渲染、设备驱动或复杂工作流仍需扩展代码。
6. **前后端与存储独立部署**：前端不可直接连数据库；后端为唯一受控写入入口。
7. **设计为可迁移的模块化单体优先**：早期少量进程/服务、边界清晰；不为了“先进”一次拆十几个微服务。
8. **Semantica 可替换**：通过 adapter 包装其能力，固定版本，避免把项目业务模型绑死在某个 Python SDK 上。
9. **OWL 推理不等于业务校验**：OWL 通常使用开放世界假设；强制必填、数值范围、审批、设备安全通过 SHACL 和后端规则完成。
10. **事实必须可追溯**：人录入、设备测量、模型建议、文档抽取都记录来源、时间、可信状态，不让 AI 结果自动成为确定事实。
11. **新建框架绝不能直接控制真实闸门/农机**：MVP 的物理操作全为模拟，后续采用独立设备网关、权限、联锁和回执机制。
12. **独立品牌和 UI**：不复用旧梁子湖本体系统前端；参考 Semantica / WorldWideView 的交互理念，不复制其配色、布局、图标、文案或组件外观。

## 1.4 不建议的做法

- 不以来源数据库 `190` 张表直接推导 `190` 个核心农业概念；“表”不等于“领域概念”。
- 不把 GitHub 上标称支持 OWL 的任意库视作“自动具备完整 OWL 2 DL 推理”。
- 不把采样值不断写入 RDF 图谱作为唯一时序库。
- 不把所有 GIS 多边形完整复制进图谱作为唯一空间数据库。
- 不把用户前端执行 `PATCH` 等同真实物理动作完成。
- 不在没有语义版本管理和数据迁移方案时允许直接修改已发布的类 IRI。
- 不为了看起来像 Palantir，而首先复制其全套产品和复杂运维架构。

---

# 2. 目标、边界、成功标准

## 2.1 业务范围

**核心空间范围**：梁子湖区内的农场、田块、水塘、水渠、农业设施、传感器、网关、摄像头、闸门等。首版地图默认定位梁子湖区，以依法可用的行政区界与经核验的空间实体数据为准。

**关联范围可向外延伸**：省级政策、国家规范、农业论文、供应商、物流流向等可以超出梁子湖边界，但需说明它们与本地对象的关联，且不应被误画成区内实体。

**农业经营领域**：耕种、养殖、水利灌溉、设备监测、病虫害事件、巡检、生产批次、质量与溯源。

**暂不做**：自研通用大语言模型、完整 CAD/数字高程重建、全国农业数据库、真实自动化农业控制闭环、未经验证的农业诊断与预测。

## 2.2 三个交付阶段的总体目的

| 阶段 | 目标 | 证明什么 |
|---|---|---|
| **V0：本体内核** | 本地可启动、类与属性/关系可编辑、RDF持久化、SHACL可校验、实例可查询 | 不是普通关系数据库的 UI 壳 |
| **V1：农业本体工作台** | Schema 编辑、实例管理、图谱浏览、地图关联、来源管理、版本发布 | 新农业对象可以以元数据驱动方式扩展 |
| **V2：Operational Ontology** | Action Type、审批、写回适配器、状态/审计、多个业务系统一致读写 | 不只是“认识世界”，还可以治理业务操作 |

## 2.3 可量化的 V0 验收门槛（建议值）

- 新建 `WaterQualitySensor` 为 `Sensor` 子类，发布后可通过 RDFS/已配置推理规则查询其属于 `Sensor` 的实例。
- 新建 `monitors` 关系，禁止将不符合业务定义的端点作为**已采纳**关系写入。
- 后端至少支持：Schema 草稿/发布、实例 CRUD（停用代替危险物理删除）、关系增删、按 IRI 查询、分页列表、基本 SPARQL 只读查询、SHACL 校验报告。
- 前端选择新类后根据元数据生成表单，满足 80% 以上基础字段的零定制 CRUD；特例由插件注册机制处理。
- 重启前后，已发布 Schema、实例、关系、审核历史保持一致，不依赖内存状态。
- 抽取式/AI 建议关系必须保持 `candidate`，不可绕过审核变为 `accepted`。
- 基础功能的集成测试在真实本地持久化数据库上通过；不可仅使用 mock 认定持久化有效。
- 可以在地图点击 `POND-001`，看见其本体身份和 `monitors` 关联设备，即使地图初始数据为明确标注的合成样例。

## 2.4 数据真实度分级

所有数据记录必须包含 `data_nature`：`synthetic`（合成）、`test`（测试）、`imported_unverified`（已接入未核实）、`verified`（经授权核实）。生产演示不允许隐去标记；任何无人核查的本体抽取结果不得标记为 `verified`。

---

# 3. 参考项目的取舍与技术选型

## 3.1 参考项目矩阵

| 来源 | 真正值得借鉴 | 需要防范的局限 | 本项目采用策略 |
|---|---|---|---|
| [Semantica](https://github.com/semantica-agi/semantica) | OWL 生成、SHACL、RDF/SPARQL、多图存储、Ontology Explorer、推理/溯源模块 | 部分 Explorer 状态在 `app.state` 内存字典中；部分能力侧重从现有数据自动推断 Schema；不同存储后端能力不齐 | **主要技术参考 + 能力适配器**，不能替代正式持久化治理 |
| [Semantica Knowledge Explorer](https://github.com/semantica-agi/semantica/tree/main/explorer) | React 图谱编辑、关系筛选、时间视图、节点 Inspector、SKOS / SHACL 工作台 | 前端总体为知识图谱通用工作台而非农业管理系统 | 借鉴交互模式，**独立重写 UI** |
| [WorldWideView Demo](https://demo.worldwideview.dev/) | 地理主视图、实时图层、检索、时间轴、坐标/状态信息密度 | 全球视角及实时地理资讯体验，不适合作为完整本体编辑器 | 仅借鉴**地图沉浸感和时间+图层交互**，构建农业区域界面 |
| [Palantir Foundry Ontology](https://www.palantir.com/docs/foundry/ontology/overview/) | Object / Property / Link / Action / Function / Interface / 数据源绑定 | 闭源企业产品，架构规模远超本项目 | 参考 Operational Ontology **产品逻辑**，不复刻全套产品 |
| [Operational Ontology 参考实现](https://github.com/gura105/operational-ontology) | source-backed / ontology-owned / derived 数据权属；Action 规则、写回、审计 | 作者定位为教学参考，不是生产框架 | 借鉴 Action 边界和行为语义 |
| [旧梁子湖本体仓库](https://github.com/lianggeming14/liangzihu-ontology-traceability) | 参考它**已经列出的**农田、水塘、设备、生产批次等对象与来源字段；审视业务术语 | 自定义关系模式、无通用 OWL/SHACL 和类继承；旧前端不合设计要求 | **不设依赖、不迁前端**；仅人工复核后参考对象目录 |

**重要研究事实**：Semantica 的 `ontology` 模块可生成 OWL；其官方文档也指出完整的 OWL 语义推理可依赖外部 HermiT/Pellet/ELK 等推理器。故本方案第一阶段只承诺指定的 RDFS/规则闭包能力，且必须通过端到端测试验证，不能暗称“支持任意 OWL 2 DL 公理推理”。

## 3.2 推荐技术栈：主方案

| 层 | MVP 主方案 | 原因 / 替代方案 |
|---|---|---|
| 前端 | React + TypeScript + Vite | 独立设计、组件化、生态成熟；不引用旧仓库前端 |
| UI 基础 | Tailwind CSS + Radix/shadcn 风格的可访问性基元，自研设计令牌和组件外观 | 仅借鉴无障碍基础设施，构建自有产品语言 |
| 状态/请求 | TanStack Query + Zustand（仅页面状态） | 服务端数据缓存与交互状态分离 |
| 本体建模画布 | React Flow / XYFlow | 类型与关系编辑优于直接使用力导向图 |
| 实例关系图 | Sigma.js + Graphology（或 Cytoscape，二选一） | 用于数百节点探索；按需分层加载 |
| GIS 2D | MapLibre GL JS + PostGIS | 适配梁子湖边界与多图层；地理数据以 PostGIS 为准 |
| GIS 3D（后续） | CesiumJS 或定制可视化引擎 | 第二阶段进入农田/水塘局部 3D 时再引入 |
| 后端 | Python 3.11/3.12 + FastAPI + Pydantic v2 | 易集成 RDFLib、Semantica、pySHACL；OpenAPI 自动契约 |
| 业务主库 | PostgreSQL 16+，启用 PostGIS | 身份、元数据、Schema 发布事务、Action、权限、空间数据 |
| RDF 本体/实例图数据库 | **Oxigraph（持久磁盘目录）**，通过适配层访问 | 单机启动轻量、标准 RDF/SPARQL；可替换 Apache Jena Fuseki |
| RDF 工具 | RDFLib + pySHACL | 稳定的序列化、校验链；Semantica 提供增值能力 |
| Semantica | 固定版本集成 `ontology`、`reasoning`、`triplet_store` 等**经实测可用的子模块** | 不直接用它的 Explorer 内存工作流当生产后端 |
| 缓存/任务 | MVP 同步处理 + PostgreSQL outbox；后续 Redis + worker | 降低早期运维成本；写回/导入可异步化 |
| 原件对象存储 | 本地 MinIO / 兼容 S3；初版可选 | 图片、PDF、证据原件，数据库保 URI 和校验摘要 |
| 时序数据 | 初期 PostgreSQL 普通分区表；需求增长再评估 TimescaleDB | 不将每秒采样全部写入知识图谱 |
| 认证 | OIDC/OAuth2（推荐 Keycloak 或对接现有身份服务） | 不自研密码体系；后端 RBAC + 对象级授权 |
| API | REST/OpenAPI；只读受控 SPARQL | 不让普通用户直接对 RDF 库执行 SPARQL UPDATE |
| 部署 | Docker Compose（开发）→ 容器化单实例 → 按需 K8s | 演进式，不提前过度微服务化 |

### 3.2.1 为什么选 Oxigraph 而非立即 Jena Fuseki？

本方案优先考虑第一版单机开发、标准 RDF 查询和明确的数据目录，Oxigraph 更易作为一个独立持久化容器/进程启动；Jena Fuseki/TDB2 同样合理，尤其需要 Jena 规则、Java 生态或更成熟的集群运维路径时。

**数据库抽象的唯一允许依赖是接口**，例如：`GraphRepository.query()`、`GraphRepository.update_transaction()`、`GraphRepository.put_named_graph()`、`GraphRepository.health()`；不允许业务服务直接依赖 Oxigraph 特有语法。

MVP 选型在 P0 结束时，用一组标准的导入、查询、SHACL 及崩溃恢复测试固定下来；如当前运行环境与 Semantica Oxigraph 适配器冲突，可以通过 RDFLib/SPARQL HTTP 协议实现桥接，而不是整体换框架。

### 3.2.2 为什么不是所有数据都放 RDF？

RDF 是语义图的权威表示，适合表达类、关系、稳定实体引用、证据链接和推理结果。PostgreSQL/PostGIS/时序表则更擅长严格事务、几何计算、高频读写和复杂业务操作。**“本体系统”并不等于“只许有一个图数据库”。**

## 3.3 开源许可和供应链要求

- Semantica 仓库当前是 MIT；集成时确认**选定 tag 与依赖版本对应的 LICENSE**。
- 前端复制第三方组件代码前检查原始 LICENSE，尤其是地图底图、图标、商用字体和在线影像数据。
- 锁定 Python 与前端依赖；生成 SBOM；CI 做基础漏洞扫描、依赖审计与许可清单。
- Semantica 采用依赖版本白名单，不应 `pip install -U` 随意升级线上系统；每次升级以契约测试+回归测试验收。
- WorldWideView 仅提供视觉/交互参考，不默认拥有复制其前端代码或地图素材的许可。

---

# 4. 核心概念与领域建模规则

## 4.1 术语表

| 术语 | 定义 | 示例 |
|---|---|---|
| Ontology | 领域概念及关系的正式定义 | “水质传感器是一类传感器” |
| Class / Object Type | 现实对象的类型 | `Pond`、`Sensor` |
| Subclass | “是一种（is-a）”的类层次 | `WaterQualitySensor ⊑ Sensor` |
| Instance | 具体对象 | 梁子湖 `POND-001` |
| Datatype Property | 对象指向文本、数值等字面值的属性 | `areaHa`、`name` |
| Object Property / Link Type | 两个对象之间的关系 | `monitors`、`partOf` |
| IRI | 资源全局稳定标识符 | `https://id.example.org/lzo/resource/pond-001`（示例占位域名） |
| RDF triple | 主语—谓语—宾语 | `sensor-001 monitors pond-001` |
| OWL | 描述类层次、公理、语义约束的标准 | `rdfs:subClassOf`、`owl:inverseOf` |
| SHACL | 验证 RDF 数据图是否满足约束 | 水塘名称必填、数值范围 |
| SPARQL | 查询 RDF 图的语言 | 找出监测指定水塘的设备 |
| Named Graph | 给不同用途的 RDF 三元组分命名图 | 正式事实图、候选图、推理图 |
| Object Set | 按条件选出的对象集合 | 当前告警中的所有水塘 |
| Action Type | 能被授权调用的业务操作定义 | `CreateInspectionTask` |
| Function | 不改变状态的业务计算 | `GetLatestWaterQuality` |
| Provenance | 事实来源、生成和采纳证据 | 测量来源、论文、录入人、时间 |
| Source of Truth | 特定数据字段的权威拥有者 | 几何来自 GIS、设备状态来自设备网关 |

## 4.2 必须避免的混淆

1. **子类 ≠ 实例**：`WaterQualitySensor` 是类；`SENSOR-001` 才是实例。
2. **关系 ≠ 外键本身**：原数据库 `device.field_id` 可能提示位于某田块，但不能证明其正在监测该田块。
3. **属于（partOf）≠ 位于（locatedIn）≠ 作用于（monitors/controls）**：必须独立定义。
4. **概念分类（taxonomy）≠ 所有复杂农业知识**：作物品种、传感器型号、地块经营权、事件因果等语义应分别处理。
5. **OWL 的 `rdfs:domain` / `rdfs:range` 有推理语义**：不能简单把它们理解为普通数据库“阻止错误输入”的约束；输入禁止由 SHACL/业务服务做。
6. **“没有断言”≠“断言为假”**：农业状态存在缺测、未知、冲突、撤回、过期，模型必须允许明确的不确定状态。

## 4.3 统一资源命名规范

正式域名须由项目控制。以下示例使用**占位域名**，部署时必须统一替换，并保持永久重定向/解析策略。

```text
https://id.example.org/lzo/ontology/core#Pond
https://id.example.org/lzo/ontology/device#WaterQualitySensor
https://id.example.org/lzo/property#monitors
https://id.example.org/lzo/resource/pond/8c5f... (UUID)
https://id.example.org/lzo/action#CreateInspectionTask
```

- 术语 IRI：稳定、可读、采用 English PascalCase；不要把中文显示名当唯一键。
- 实例 IRI：内部 UUID（推荐 UUIDv7 或 UUIDv4）+ 业务可读别名；不以会变化的设备编号作为主键。
- `schema_version`：语义定义版本；`entity_revision`：具体实体修订版本；两者独立。
- 中文、英文、别名：使用 `rdfs:label`（语言标签）及 `skos:altLabel`；保留业务部门熟悉的中文名称。
- 外部实体 ID：使用 `source_bindings` 映射；未确认等价不可贸然添加 `owl:sameAs`。

---

# 5. 总体架构与系统边界

## 5.1 核心架构图

```mermaid
flowchart TB
  U[Web 用户 / 运维人员 / 研究人员] --> FE[独立 React 前端 · LZO Workbench]
  FE --> BFF[FastAPI · 统一网关与鉴权]
  BFF --> CAT[Ontology Registry / Schema Service]
  BFF --> OBJ[Object & Link Service]
  BFF --> QRY[Graph Query & Inference Service]
  BFF --> ACT[Action / Function Service]
  BFF --> CON[Source Connector / Provenance Service]
  CAT --> PSQL[(PostgreSQL · 元数据/版本/身份/审计)]
  CAT --> RDF[(持久 RDF Store · OWL/SHACL/事实图)]
  OBJ --> PSQL
  OBJ --> RDF
  QRY --> RDF
  QRY --> GEO[(PostGIS · 地块/水体/设施几何)]
  ACT --> PSQL
  ACT --> DEV[设备/业务适配器 · MVP 仅模拟]
  CON --> PSQL
  CON --> RDF
  CON --> OBJSTORE[(MinIO / S3 · 原件与证据)]
  CON --> TS[(PostgreSQL 时序分区表)]
  EXT[原数字孪生平台 / 传感器 / 文档 / 政策 / 论文] --> CON
  ACT --> OUTBOX[事务 Outbox / 任务执行]
  OUTBOX --> DEV
```

> 注意：图中的服务是**逻辑边界**，不是要求 V0 部署为七八个微服务。建议 V0 使用一个 FastAPI 进程、一个 worker（必要时）、PostgreSQL、RDF Store、可选 MinIO，共享 TypeScript 前端。明确包/模块边界后再拆服务。

## 5.2 后端模块职责

| 模块 | 负责 | 不负责 |
|---|---|---|
| Ontology Registry | 草稿、版本、类/属性/关系/Shape、依赖、发布校验 | 真实设备命令、时序采样 |
| Object Service | 实体身份、生命周期、普通属性更新、分页、权限 | 推理引擎具体实现 |
| Link Service | 关系约束、方向、有效时间、证据、候选/已采纳 | 自动将所有来源外键认定为业务关系 |
| Semantic Engine | RDF/OWL 序列化、SHACL、已声明的推理策略、SPARQL 只读 | 设备操作审批 |
| Provenance / Connector | 来源 ID 映射、数据转换、快照、批次重放、证据、可信状态 | 任意写回第三方数据库 |
| Geo Adapter | geometry_id 解析、空间筛选、按实体返回几何摘要 | 随意修改 GIS 主库里的多边形 |
| Telemetry Adapter | 最新读数、聚合、时段曲线 | 海量观测数据写入单张 RDF 图 |
| Action Engine | 参数校验、权限、业务规则、模拟/审批/执行、审计 | 绕过设备侧联锁直接对现场设备发控制信号 |

## 5.3 服务之间的通信策略

- 客户端只调用 `/api/v1/...`，不直接访问 Postgres、SPARQL 更新端口或设备网关。
- 数据结构以 OpenAPI + JSON Schema + OWL/SHACL Schema Release 为契约。
- 写命令先完成鉴权，再验证，最后持久化业务状态或登记外部操作请求。
- 同一逻辑实体多个存储库时明确**主写模型与派生投影**：例如对象注册/审核写 PostgreSQL，然后通过 outbox 更新 RDF 图；读取允许使用 RDF 投影，但必须携带同步水位。
- 允许最终一致的派生图谱，但界面需要展示更新时间、同步状态；动作执行不得依据过期的缓存做安全决策。
- 每次写入附带 `correlation_id`、`idempotency_key`（适用时）、`schema_version`、`actor_id`、`source_id` 和 `occurred_at`。

---
# 6. 本体 Schema 分层与农业初始模型

## 6.1 四层本体组织法（面向长期扩展）

```mermaid
flowchart TB
  BASE[Foundation Ontology<br/>标识、时间、来源、空间、组织] --> AGRI[Core Agriculture Ontology<br/>Farm / Zone / Crop / Device / Operation]
  AGRI --> FIELD[Crop & Field Module<br/>农田、种植、土壤、农事]
  AGRI --> AQUA[Aquaculture Module<br/>水塘、水质、水产、投喂]
  AGRI --> WATER[Water & Irrigation Module<br/>水渠、闸门、灌溉、泵站]
  AGRI --> TRACE[Traceability Module<br/>批次、加工、转运、检测]
  AGRI --> INTEL[Information Module<br/>政策、论文、资讯、适用对象]
  FIELD --> INSTANCE[Liangzihu Instance Graph<br/>真实/模拟场景对象]
  AQUA --> INSTANCE
  WATER --> INSTANCE
  TRACE --> INSTANCE
  INTEL --> INSTANCE
```

建议领域包与命名空间：

| 包名 | IRI 示例 | 核心内容 |
|---|---|---|
| `lzo-foundation` | `/ontology/foundation#` | 身份、空间、事件、证据、来源、时间 |
| `lzo-agri-core` | `/ontology/agri-core#` | Farm、Zone、Crop、Device、Observation、Operation |
| `lzo-crop` | `/ontology/crop#` | Field、RicePaddy、CropCycle、SoilSample、Fertilization |
| `lzo-aquaculture` | `/ontology/aquaculture#` | Pond、AquacultureCycle、WaterQualitySensor、FeedingEvent |
| `lzo-irrigation` | `/ontology/irrigation#` | Channel、WaterGate、Pump、IrrigationTask |
| `lzo-traceability` | `/ontology/traceability#` | ProductionBatch、MaterialLot、Shipment、QualityCheck |
| `lzo-intelligence` | `/ontology/intelligence#` | SourceDocument、Policy、Publication、Claim、AppliesTo |
| `lzo-ui-metadata` | `/ui/schema/`，**不混入 OWL 核心语义** | 表单分组、字段控件、图标、工作台页面默认布局 |

术语稳定后不要频繁改变核心 IRI。包装/模块只是组织单元，不强制映射为业务 `subClassOf`。

## 6.2 建议的首批核心类（TBox）

**第一阶段保留有限、准确的类**。以下为建议而非经现场认证的完整农业本体标准。

| Class | 父类 | 中文定义 | 常见属性 | 与其他类关系 |
|---|---|---|---|---|
| `AgriculturalEntity` | `owl:Thing` | 农业系统可识别的基础实体 | `displayName`, `status`, `externalId` | — |
| `AgriculturalZone` | `AgriculturalEntity` | 农业地理区域或经营分区 | `areaHa`, `geometryRef` | `partOf Farm` |
| `Farm` | `AgriculturalEntity` | 农场或经营主体对应场所 | `name`, `operator` | `containsZone` |
| `Field` | `AgriculturalZone` | 田块 | `areaHa`, `soilType` | `plantedWith Crop` |
| `RicePaddy` | `Field` | 水稻田块 | `waterLevelTarget` | `irrigatedBy Channel` |
| `Pond` | `AgriculturalZone` | 水塘 / 养殖水体 | `areaHa`, `pondCode` | `monitoredBy Sensor` |
| `Channel` | `AgriculturalEntity` | 沟渠或输水设施 | `lengthM` | `connectsTo Channel`, `suppliesWaterTo Zone` |
| `Facility` | `AgriculturalEntity` | 温室、泵站等设施 | `facilityType` | `locatedIn Zone` |
| `Device` | `AgriculturalEntity` | 具有身份和状态的设备 | `model`, `serialNumber` | `locatedIn Zone` |
| `Sensor` | `Device` | 测量设备 | `metricType`, `unit` | `monitors Zone` |
| `WaterQualitySensor` | `Sensor` | 水质传感器 | `measurementCapabilities` | `monitors Pond` |
| `Actuator` | `Device` | 执行控制设备 | `actuationType` | `controls Facility` |
| `WaterGate` | `Actuator` | 水闸设备 | `reportedOpeningPct` | `installedOn Channel` |
| `Crop` | `AgriculturalEntity` | 作物种类/种植对象 | `scientificName` | `plantedOn Field` |
| `Observation` | `AgriculturalEntity` 或采用 SOSA 模型 | 观测事件摘要 | `observedAt`, `resultValue` | `observedBy Sensor` |
| `AgriculturalOperation` | `AgriculturalEntity` | 农事作业 | `plannedAt`, `executedAt` | `performedOn Zone` |
| `ProductionBatch` | `AgriculturalEntity` | 生产批次 | `batchCode`, `startDate` | `originatedFrom Zone` |
| `Evidence` | `AgriculturalEntity` 或 PROV 模型 | 原始证据/来源记录 | `checksum`, `capturedAt` | `supports Claim` |
| `InspectionTask` | `AgriculturalOperation` | 现场巡检任务 | `dueAt`, `status` | `targets Entity` |
| `Alarm` | `AgriculturalEntity` | 告警事件 | `severity`, `raisedAt` | `concerns Device/Zone` |

**专业建模说明**：`Observation` 建议优先对齐 W3C SOSA/SSN；`Evidence`、`Claim` 与 `PROV-O` 对齐。对于 `Crop` 必须区分“作物品种概念/词表”和“一片田里的一茬实际作物”；严格业务需求下应引入 `CropTaxon` 与 `CropCycle`，避免把水稻概念当作一株物理对象。

## 6.3 第一版核心关系定义（Object Properties）

| Predicate | Domain → Range | 方向和含义 | 约束/注意 |
|---|---|---|---|
| `partOf` | Zone → Farm | 区域属于经营单元 | 不等价于几何包含；引用真实管理关系 |
| `locatedIn` | Device → Zone | 设备位置归属 | 不代表传感器监测该区域 |
| `monitors` | Sensor → Zone | 传感器监测区域 | 对水质传感器可进一步约束目标为 Pond |
| `controls` | Actuator → Facility/Device | 执行器控制目标 | 不暗示当下正执行控制 |
| `installedOn` | WaterGate → Channel | 闸门安装在渠道 | 空间/设备结构关系 |
| `suppliesWaterTo` | Channel → Zone | 水渠有供水业务关系 | 不代表当前水流方向/状态 |
| `plantedWith` | Field → Crop | 农田当前作物类别 | 最好给有效期并关联 CropCycle |
| `originatedFrom` | ProductionBatch → Zone | 批次来源区域 | 需可审核来源证据 |
| `hasObservation` | Entity → Observation | 对象关联观测摘要 | 大量时序明细由时序库持有 |
| `producedBy` | MaterialLot → ProductionBatch | 物料与生产批次关系 | 追溯需要时间/来源 |
| `derivedFrom` | MaterialLot → MaterialLot | 批次加工派生关系 | 防止循环物料谱系 |
| `concerns` | Alarm → Device/Zone | 告警涉及对象 | 可一对多 |
| `supportedBy` | Claim → Evidence | 断言有证据支持 | 候选与已采纳语义分离 |
| `appliesTo` | Policy/Publication → Crop/Zone | 政策文献适用对象 | 初始均为候选，人工审核 |

建议每个 Link Type 附带：稳定 IRI、中文/英文名、方向、逆向显示名、可连接类型、最大/最小基数（业务层）、生命周期策略、是否需要证据、是否要求人工采纳、是否可通过 Action 编辑。

## 6.4 本体模块与实例的独立演进

**示例**：增加 `AmmoniaNitrogenSensor`（氨氮传感器）：

1. 本体编辑器中选择 `Sensor` 作为父类，设置新类 IRI 和中文标签。
2. 增加 `measures` 可关联指标类型，增加对应 SHACL 数据校验。
3. 预览继承的通用属性（厂家、位置、状态），并显示新增属性。
4. Schema 草稿发布；需兼容校验并记录谁批准。
5. 在实例管理器新建具体设备实例（ID 自动生成），绑定某个水塘。
6. 地图、实例列表、图谱浏览器无需写新列表页即可发现该实例。
7. 真正对接设备协议/读取寄存器则由 IoT 适配器单独开发，不能仅通过新类定义自动完成。

## 6.5 SKOS 与词汇表

适合用 SKOS（而非过度堆砌 OWL 子类）的对象：作物类别目录、水质指标目录、告警等级、设备品牌、农业政策类别。不要把每个传感器型号都升为一个 OWL Class；优先通过 `skos:Concept` 或实例属性表达型号，除非确实需要独立逻辑约束或业务行为。

---

# 7. RDF、OWL、SHACL、SPARQL 设计

## 7.1 规范的文件组织

```text
ontology/
  catalog.yaml                    # 本体包版本、依赖、命名空间、适用环境
  foundation/
    1.0.0/ontology.ttl
    1.0.0/shapes.ttl
  core/
    1.0.0/ontology.ttl
    1.0.0/shapes.ttl
  aquaculture/
    1.0.0/ontology.ttl
    1.0.0/shapes.ttl
  agriculture/
    1.0.0/ontology.ttl
    1.0.0/shapes.ttl
  examples/
    synthetic-pond-sensor.ttl
    invalid-pond-sensor.ttl
  test_queries/
    monitoring_by_pond.rq
  migrations/
    v1.0.0_to_v1.1.0.md
  tests/
    test_schema_contracts.py
```

所有 `ontology.ttl`、`shapes.ttl` 均应可审查；正式发布时保存文件 SHA-256、作者、审核人、发布时间及依赖包清单。正式发布内容不可原位改写；变更发布新版本。可允许草稿以 UI 编辑并生成 Turtle 预览。

## 7.2 可用的 OWL / RDF 示例（Turtle）

> 以下示例使用 `.example.org` 占位 IRI。工程初始化时需配置真实且可长期使用的标识域名，不要发布到正式环境后才批量更名。

```turtle
@prefix lzo:  <https://id.example.org/lzo/ontology/core#> .
@prefix dev:  <https://id.example.org/lzo/ontology/device#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl:  <http://www.w3.org/2002/07/owl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .

lzo:AgriOntology a owl:Ontology ;
    rdfs:label "梁子湖农业核心本体"@zh .

lzo:AgriculturalEntity a owl:Class ;
    rdfs:label "农业实体"@zh .

lzo:AgriculturalZone a owl:Class ;
    rdfs:subClassOf lzo:AgriculturalEntity ;
    rdfs:label "农业分区"@zh .

lzo:Pond a owl:Class ;
    rdfs:subClassOf lzo:AgriculturalZone ;
    rdfs:label "水塘"@zh .

dev:Device a owl:Class ;
    rdfs:subClassOf lzo:AgriculturalEntity .

dev:Sensor a owl:Class ;
    rdfs:subClassOf dev:Device .

dev:WaterQualitySensor a owl:Class ;
    rdfs:subClassOf dev:Sensor ;
    rdfs:label "水质传感器"@zh .

lzo:displayName a owl:DatatypeProperty ;
    rdfs:domain lzo:AgriculturalEntity ;
    rdfs:range xsd:string ;
    rdfs:label "显示名称"@zh .

dev:monitors a owl:ObjectProperty ;
    rdfs:domain dev:Sensor ;
    rdfs:range lzo:AgriculturalZone ;
    rdfs:label "监测"@zh .
```

**警惕 domain/range 的语义**：如果输入 `x dev:monitors y`，RDFS 推理可以据此推断 `x` 是 `Sensor`、`y` 是 `AgriculturalZone`；其本身不保证拒绝错误输入。因此业务级端点类型必须用 SHACL 或服务器检查。

## 7.3 可用的实例 RDF 示例

```turtle
@prefix lzo: <https://id.example.org/lzo/ontology/core#> .
@prefix dev: <https://id.example.org/lzo/ontology/device#> .
@prefix res: <https://id.example.org/lzo/resource/> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .

res:pond-001 a lzo:Pond ;
    lzo:displayName "演示水塘 001" .

res:sensor-001 a dev:WaterQualitySensor ;
    lzo:displayName "演示水质传感器 001" ;
    dev:monitors res:pond-001 .
```

本实例数据属于**合成场景**；正式数据应使用经系统生成的 UUID IRI，而不是永久以演示编号为主键。

## 7.4 可用的 SHACL 约束示例

```turtle
@prefix sh:   <http://www.w3.org/ns/shacl#> .
@prefix xsd:  <http://www.w3.org/2001/XMLSchema#> .
@prefix lzo:  <https://id.example.org/lzo/ontology/core#> .
@prefix dev:  <https://id.example.org/lzo/ontology/device#> .

lzo:PondShape a sh:NodeShape ;
    sh:targetClass lzo:Pond ;
    sh:property [
      sh:path lzo:displayName ;
      sh:datatype xsd:string ;
      sh:minCount 1 ;
      sh:maxCount 1 ;
      sh:message "水塘必须有且仅有一个显示名称"@zh
    ] .

dev:WaterQualitySensorShape a sh:NodeShape ;
    sh:targetClass dev:WaterQualitySensor ;
    sh:property [
      sh:path dev:monitors ;
      sh:class lzo:Pond ;
      sh:minCount 1 ;
      sh:message "水质传感器至少应关联一个水塘"@zh
    ] .
```

**在创建草稿时允许不完整**：例如水质传感器还没确定监测水塘，可先处于 `draft` 状态，发布为 `active` 前必须通过严格 Shape。部署时可按图/实例状态采用不同 validation profile（草稿宽松、生产严格）；不能让一个严苛约束令真实数据接入永远无法处理部分记录。

**注意继承**：SHACL 是约束，不是自动实现所有 OWL 继承语义的“魔法工具”。验证时必须确保待验证图包含适当的类型层次或已经按照规定执行 RDFS/OWL 预推理；同时需要为继承约束定义测试，包括子类多重父类、缺失类型与循环继承检查。

## 7.5 SPARQL 查询示例

```sparql
PREFIX lzo: <https://id.example.org/lzo/ontology/core#>
PREFIX dev: <https://id.example.org/lzo/ontology/device#>

SELECT ?sensor ?name WHERE {
  ?sensor dev:monitors <https://id.example.org/lzo/resource/pond-001> .
  OPTIONAL { ?sensor lzo:displayName ?name . }
}
ORDER BY ?sensor
LIMIT 100
```

查询复杂度约束：默认 `LIMIT 100`；允许配置上限、超时、图访问白名单、查询深度及资源预算。普通用户只读 SPARQL；写入只能通过领域 API，防止绕过权限、审计和 SHACL。

## 7.6 Graph 分区（Named Graph）

建议定义**逻辑命名图**：

```text
urn:lzo:graph:schema:published:v1.0.0
urn:lzo:graph:instances:accepted
urn:lzo:graph:instances:candidate
urn:lzo:graph:instances:derived
urn:lzo:graph:provenance
urn:lzo:graph:imports:2026-10-20-001
```

真正的隔离依赖 RDF Store 访问策略及查询服务强制绑定，不能只靠图名防止越权。候选图 `candidate` 永不默认并入运营决策查询；派生图需有规则版本和重算策略。

## 7.7 推理策略：从有限确定能力起步

第一版建议只承诺：

- `rdfs:subClassOf` 的传递闭包及实例类型推断。
- 经显式声明的 `owl:inverseOf`，若使用则需精确定义实现支持范围。
- 有限的 `rdfs:subPropertyOf` 推理（如果业务确实需要）。
- 业务规则应明确由规则引擎执行，不能混同为 OWL 语义推理。

每条推理结果必须可解释：**原始事实 + 适用规则 + 规则版本 + 推理时间**。若事实撤回，派生结论需重算并失效，不能只会增加推理三元组不会删除。

复杂 OWL 2 推理留至后续独立评估；如果需要 HermiT/ELK 等，将其作为可选推理服务，而非早期平台运行必需项。

## 7.8 Schema 的质量门禁

每次发布必须执行：IRI 格式检查、重复和冲突标识检查、语义类循环检测、外部模块依赖版本检查、是否破坏现有数据校验、SHACL 预验证、查询回归测试、弃用字段影响统计、Schema diff 预览及审核通过。系统保存发布者、变更原因与回滚方案。

---

# 8. 数据库与知识图谱持久化设计

## 8.1 权威数据矩阵（必须明确）

| 数据 | 唯一/主要权威存储 | RDF 中保留什么 | 变更路径 |
|---|---|---|---|
| 发布的本体 Schema | Git + PostgreSQL 发布注册表 + 持久化 RDF Schema 图（组合契约） | OWL、SHACL 定义 | Schema 管理发布流水线 |
| 平台新建实体身份与修订 | PostgreSQL `entities` | 类型、可查询的稳定属性、关系 | Object API / Event Outbox |
| 业务实体之间已采纳关系 | PostgreSQL `entity_links` 审计记录 | RDF Accepted Graph 投影 | Link API；审核后同步 |
| 本体语义图谱 | RDF Store | 发布 Schema、接受/候选/推断事实 | 仅 Semantic Adapter / 发布任务 |
| 地块与水体 geometry | PostGIS | `geometryRef` / 几何摘要，必要时 GeoSPARQL 表达 | GIS Adapter |
| 传感器历史读数 | PostgreSQL 分区时序表 | 设备测量能力、指标定义、最近观测摘要/事件链接 | Telemetry Ingestion |
| 设备实时状态 | 设备平台或 IoT 适配服务 | 带 `observedAt` 的快照引用 | Device Service / 回执 |
| 原始证据文件 | MinIO/S3 | `prov:wasDerivedFrom`、文件 URI / checksum | Provenance Service |
| Action 指令和历史 | PostgreSQL Action 表 + 审计表 | 必要时映射为事件/溯源事实 | Action Engine |
| 外部文档 / 情报 | 来源原件库 + PostgreSQL 索引 | 经审核的实体/论断及关系 | Intelligence Adapter |

**一致性规则**：PostgreSQL 对应用写事务负责，RDF 是语义查询权威图谱，但对象创建/关系审核等通过 outbox 逐步投影。不要对跨 PostgreSQL + RDF 的两库提交做未经设计的分布式“强一致”承诺。用户界面需能标识 `projection_status=pending|synced|failed`。

## 8.2 PostgreSQL 最小 Schema（示例 DDL）

以下是**启动设计骨架**，不是可直接生产上线的完整迁移。真实代码还需 migration 编号、外键、权限、索引、分区、删除策略和事务隔离测试。

```sql
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE ontology_releases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  package_key text NOT NULL,
  version text NOT NULL,
  status text NOT NULL CHECK (status IN ('draft','review','published','deprecated')),
  base_iri text NOT NULL,
  owl_sha256 text,
  shacl_sha256 text,
  manifest_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_by text NOT NULL,
  published_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE (package_key, version)
);

CREATE TABLE entities (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  canonical_iri text NOT NULL UNIQUE,
  class_iri text NOT NULL,
  schema_version text NOT NULL,
  display_name text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  data_nature text NOT NULL DEFAULT 'synthetic',
  properties jsonb NOT NULL DEFAULT '{}'::jsonb,
  revision bigint NOT NULL DEFAULT 1,
  created_by text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  deactivated_at timestamptz
);

CREATE TABLE entity_links (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  subject_entity_id uuid NOT NULL REFERENCES entities(id),
  predicate_iri text NOT NULL,
  object_entity_id uuid NOT NULL REFERENCES entities(id),
  status text NOT NULL CHECK (status IN ('candidate','accepted','rejected','withdrawn')),
  valid_from timestamptz,
  valid_to timestamptz,
  evidence_refs jsonb NOT NULL DEFAULT '[]'::jsonb,
  revision bigint NOT NULL DEFAULT 1,
  created_at timestamptz NOT NULL DEFAULT now(),
  CHECK (subject_entity_id <> object_entity_id OR predicate_iri <> 'https://id.example.org/lzo/property#partOf')
);

CREATE TABLE source_bindings (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  entity_id uuid NOT NULL REFERENCES entities(id),
  source_system text NOT NULL,
  source_table text NOT NULL,
  source_key text NOT NULL,
  binding_status text NOT NULL DEFAULT 'active',
  provenance_json jsonb NOT NULL DEFAULT '{}'::jsonb,
  UNIQUE (source_system, source_table, source_key)
);

CREATE TABLE audit_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  correlation_id text NOT NULL,
  actor_id text NOT NULL,
  action text NOT NULL,
  target_iri text,
  before_json jsonb,
  after_json jsonb,
  occurred_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE outbox_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL,
  aggregate_id uuid NOT NULL,
  aggregate_revision bigint NOT NULL,
  payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  published_at timestamptz,
  attempt_count integer NOT NULL DEFAULT 0
);
```

应另建：`entity_revisions`、`ontology_drafts`、`ontology_approvals`、`action_definitions`、`action_executions`、`action_execution_events`、`evidence`、`source_import_jobs`、`spatial_features`、`telemetry_readings`、`permission_grants`、`projection_checkpoints`。这些表应由相应模块迁移文件负责，不能单靠 JSONB 无约束增长。

## 8.3 RDF Store 的写入及快照

- 不直接让 UI 操作 SPARQL UPDATE。
- 后端发布 Schema 时完整安装对应版本的 Named Graph，**仅在校验通过后**把该版本标为生产可用。
- 对象增改后写事务 Outbox，由同步 worker 将 `EntityChanged` 映射成 RDF diff；使用 `(aggregate_id, aggregate_revision)` 幂等推进。
- RDF Store 的对象实例投影要能从 PostgreSQL 的事件/快照重建。
- 每日导出 RDF 数据集（N-Quads/TriG）、Schema Turtle、PG 备份及 manifest，保存 checksum 与快照时间。
- 明确 Schema 版本生效时间；回滚不直接删除历史版本，需要同步考虑实例是否兼容。

## 8.4 Geometry 与坐标治理

- 权威几何统一保存到 PostGIS，`geom geometry(Geometry, 4326)` 可作内部标准；需要按用途投影至合适坐标系计算面积与距离，不能直接在 EPSG:4326 经度纬度上做米制面积。
- 对来源 `CGCS2000 / EPSG:4490`、WGS84、GCJ-02（高德/腾讯常见）坐标区别进行数据字典管理，转换过程应记录来源和方法，禁止随意“坐标偏移修正”。
- 真正地图边界需要正式地理数据来源、授权与审核；在未获得数据前以合成几何演示并标注。
- GIS 对象 `geometry_id` 绑定统一 `entity_id`；几何变动不改变实体身份。
- GeoSPARQL 在 V0 主要作为 RDF 地理语义词汇表，几何关系和碰撞/距离计算实际由 PostGIS 提供；后续如果 RDF Store 支持相应函数再提升 GeoSPARQL 查询能力。

## 8.5 时序存储示例

```sql
CREATE TABLE telemetry_readings (
  sensor_entity_id uuid NOT NULL REFERENCES entities(id),
  metric_iri text NOT NULL,
  observed_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  numeric_value numeric,
  unit text,
  quality_flag text NOT NULL DEFAULT 'unknown',
  source_event_id text NOT NULL,
  PRIMARY KEY (sensor_entity_id, metric_iri, observed_at, source_event_id)
) PARTITION BY RANGE (observed_at);
```

按月/周创建分区（以采样量决定）；原始时序保留策略、异常值规则、单位标准化要在业务上线前由运营方确认。知识图谱只放“该传感器监测什么指标、关联哪个空间对象、最新有效观测摘要 URI”等事实，不无限展开海量采样点。

---
# 9. 实体身份、数据接入和同步治理

## 9.1 先建本体，再接真实数据；但两者并行迭代

不应该等待全部生产数据库清洗完成才构建 Schema，也不应该一次把来源 190 张表全部映射为核心农业类。

建议顺序：

1. 确定 10～20 个真正有业务语义的核心类（见 §6）。
2. 用合成实例验证 RDF/OWL/SHACL、关系查询、地图定位、动态表单。
3. 选择**一张田块来源表、一张水塘来源表、一张设备来源表**做真正的数据映射试点。
4. 在人工确认映射含义后扩大来源接入；保留未映射/待核验字段而非强行自动分类。
5. 逐步接入设备观测、政策/论文与溯源业务。

## 9.2 每个实例要有的元数据

```json
{
  "entity_id": "e8424e77-01bf-4ed8-ac55-f07534508aab",
  "iri": "https://id.example.org/lzo/resource/e8424e77-01bf-4ed8-ac55-f07534508aab",
  "class_iri": "https://id.example.org/lzo/ontology/core#Pond",
  "schema_version": "1.0.0",
  "display_name": "演示水塘 001",
  "data_nature": "synthetic",
  "status": "active",
  "source_owner": "ontology",
  "source_bindings": [],
  "properties": {"pondCode": "DEMO-POND-001"},
  "revision": 1,
  "valid_from": "2026-10-20T00:00:00+08:00",
  "valid_to": null,
  "updated_at": "2026-10-20T00:00:00+08:00"
}
```

时间是示例值，不代表现场采样数据。业务时间 `valid_from`、系统记录时间 `recorded_at` 与观测时间 `observed_at` 必须分别定义；将来需要双时态查询时才引入完整的 bitemporal 模型，而不是随处堆时间字段。

## 9.3 数据所有权（Operational Ontology 核心）

| 属性/资源 | 所有权类型 | 权威系统 | 改动方式 |
|---|---|---|---|
| 农田实际边界 | `source-backed` | GIS 原始管理服务 / PostGIS | GIS 变更流程后投影到本体 |
| 设备当前真实开度 | `source-backed` | 设备网关/PLC 回读 | 发送动作请求并等现场回执 |
| 水塘中文描述、标签 | `ontology-owned` | LZO Object Service | Object API 事务修改 |
| 一小时平均水温 | `derived` | Telemetry Aggregation | 每次计算或缓存，不能手工写值覆盖 |
| 来源论文对应作物 | `candidate/inferred` | Intelligence Adapter 提议 | 证据审核后采纳关系 |
| Schema 类的 IRI 与版本 | `schema-owned` | Ontology Registry | 审核发布，不通过普通实例编辑 |

**无主状态必须报错**：新 Property 必须声明 `owner_policy`，否则后台不允许标记为可写；基础的 `read_only`, `ontology_owned`, `source_backed`, `derived` 定义在 Schema 发布时校验。

## 9.4 数据接入流水线

```mermaid
flowchart LR
  SRC[来源 DB/API/CSV/设备事件] --> PROF[Source Profile<br/>字段、类型、编码、坐标]
  PROF --> PRE[Preview / Dry Run]
  PRE --> VAL[Schema + 业务校验]
  VAL --> ID[实体身份映射与去重候选]
  ID --> CAND[候选实体/关系 + 证据]
  CAND --> REVIEW{需要审核?}
  REVIEW -- 是 --> HUM[人工审核]
  REVIEW -- 否 --> ACCEPT[采纳]
  HUM --> ACCEPT
  ACCEPT --> PG[(PG 持久化+Outbox)]
  PG --> RDF[(RDF/Graph 投影)]
```

### 输入约束

- source profile 精确指定字段映射 `column -> property_iri`、实体 `table -> candidate_class_iri`、值转换、单位、编码、空值和敏感字段策略。
- 强制一条来源身份键：`source_system + source_instance + table + canonical_primary_key`；同名不能自动合并。
- `preview` 不修改生产数据；`apply` 必须明确确认，产生批次 ID、每行错误回执、变更统计。
- 不允许将敏感字段（密码、API Token、私人身份材料）进入公共知识图谱。
- 外部数据更新应只修改它所拥有的属性，不覆盖本体管理员的运营注释。
- 批处理幂等：相同批次/内容重放返回上次结果，不生成新实体；同一 ID 不同内容应返回冲突。
- 采纳/撤回操作产生可审计事件，推理和缓存订阅事件更新。

## 9.5 来源关系与可信状态

`candidate` → `accepted` / `rejected` → 必要时 `withdrawn`。

业务用户可以看到来源证据和审核理由；可疑自动映射/抽取不能直接驱动现场设备动作，也不能自动把水质异常结论作为正式检测报告。

## 9.6 兼容旧梁子湖仓库的正确方法

1. 从旧项目的本体包/字段定义导出目录，去重后人工分成：**标准农业类、实际实体类型、业务字段、来源表记录类、枚举/概念词表**。
2. 只把经过确认、独立于原数据库物理设计的概念纳入 `lzo-core`。
3. 不复制旧仓库的 HTML/CSS/JS；不直接导入其已发布实体为真实数据；不将该仓库设为新系统运行时依赖。
4. 后续确实需要迁移旧源数据时，通过只读导出 + Source Profile + 审核接入新系统；保留 ID binding 与迁移报告。
5. 旧仓库的 SQL/表结构仅供了解来源系统，不作为新 Ontology Platform 的总体数据模型。

---

# 10. 服务设计与接口契约

## 10.1 推荐 API 资源层级

基础路径：`/api/v1`，响应统一 envelope：`data`、`meta`、`errors`（失败时）、`trace_id`。

| 域 | 方法与路径 | 用途 | V0 优先级 |
|---|---|---|---|
| Schema | `GET /ontology/packages` | 包列表和版本 | P0 |
| Schema | `POST /ontology/drafts` | 建立本体变更草稿 | P0 |
| Schema | `PATCH /ontology/drafts/{draftId}` | 变更类/属性/关系定义 | P0 |
| Schema | `POST /ontology/drafts/{draftId}/validate` | 发布前 OWL + SHACL + 兼容性检查 | P0 |
| Schema | `POST /ontology/drafts/{draftId}/submit` | 提交审核 | P1 |
| Schema | `POST /ontology/releases` | 经授权原子发布 Schema | P0（单管理员初期） |
| Schema | `GET /ontology/releases/{releaseId}/diff` | 版本差异及影响 | P1 |
| Schema | `GET /ontology/classes/{classIri}` | 类详情和继承字段 | P0 |
| Schema | `GET /ontology/classes/{classIri}/form-schema` | 动态表单 schema | P0 |
| Object | `POST /objects` | 创建实例 | P0 |
| Object | `GET /objects?classIri=&status=&cursor=` | 条件、分页查询 | P0 |
| Object | `GET /objects/{entityId}` | 对象详情 | P0 |
| Object | `PATCH /objects/{entityId}` | 乐观锁属性修改 | P0 |
| Object | `POST /objects/{entityId}/deactivate` | 受控停用 | P0 |
| Link | `POST /links` | 创建关系（校验端点） | P0 |
| Link | `GET /objects/{entityId}/links?direction=both` | 关系及关联对象列表 | P0 |
| Link | `POST /links/{linkId}/review` | 审核接受/拒绝/撤回 | P1 |
| Graph | `GET /graph/neighborhood?entityId=&depth=&limit=` | 图谱局部加载 | P0 |
| Graph | `POST /graph/query` | 受控模板查询 | P0 |
| Graph | `POST /graph/sparql` | 只读 SPARQL，管理员/开发权限 | P1 |
| Validation | `POST /validate/instance` | 预览 SHACL 报告 | P0 |
| GIS | `GET /spatial/zones?bbox=&kind=` | 地图几何对象 | P1 |
| GIS | `GET /spatial/entities/{entityId}` | 对象空间关联 | P1 |
| Ingestion | `POST /sources/profiles` | 注册来源字段映射 | P1 |
| Ingestion | `POST /sources/jobs/preview` | 数据接入预检 | P1 |
| Ingestion | `POST /sources/jobs/apply` | 有审计的正式接入 | P1 |
| Actions | `GET /action-types` | 可用操作定义 | P2 |
| Actions | `POST /actions/{actionKey}/validate` | 动作参数与业务规则预检查 | P2 |
| Actions | `POST /actions/{actionKey}/execute` | 产生动作执行请求 | P2 |
| Actions | `GET /action-executions/{executionId}` | 动作执行进度、回执和审计 | P2 |
| System | `GET /health/live` 与 `/health/ready` | 存活与依赖健康 | P0 |

**URI 规则**：路径中表示 `classIri`、`linkIri` 等的参数需要 URL 编码；对于复杂 IRI 也可使用 query body，避免带 `#`、`/` 的路由解析错误。

## 10.2 动态创建类与实例的契约分离

**示例 1：新增 Object Type 的草稿 patch**

```json
{
  "draft_id": "draft-20261020-001",
  "base_release": "lzo-agri-core@1.0.0",
  "operations": [
    {
      "op": "add_class",
      "iri": "https://id.example.org/lzo/ontology/device#AmmoniaNitrogenSensor",
      "parent_iri": "https://id.example.org/lzo/ontology/device#Sensor",
      "labels": {"zh": "氨氮传感器", "en": "Ammonia Nitrogen Sensor"}
    }
  ],
  "reason": "新增养殖水质指标设备类型"
}
```

**示例 2：创建对象实例**

```json
{
  "class_iri": "https://id.example.org/lzo/ontology/device#WaterQualitySensor",
  "display_name": "演示氨氮水质测点",
  "data_nature": "synthetic",
  "attributes": {"serialNumber": "DEMO-SENSOR-001"},
  "links": [
    {"predicate_iri": "https://id.example.org/lzo/ontology/device#monitors", "object_entity_id": "pond-uuid"}
  ]
}
```

服务端以发布版本中生效的 SHACL 与权限判定；不要相信前端传来的 `class_iri`、类型、值单位和表单合法性检查。

## 10.3 错误码必须机器可读

| 码 | HTTP | 触发条件 |
|---|---|---|
| `ONTOLOGY_CLASS_NOT_FOUND` | 404 | 类不存在/无权访问 |
| `SCHEMA_VERSION_CONFLICT` | 409 | 基线版本或发布版本已变化 |
| `SHACL_VIOLATION` | 422 | 数据不满足 SHACL |
| `LINK_ENDPOINT_INCOMPATIBLE` | 422 | 起点/终点与 Link Type 规则不匹配 |
| `ENTITY_REVISION_CONFLICT` | 409 | 对象被并发更新 |
| `SOURCE_OWNED_PROPERTY` | 403 | 直接编辑上游拥有的属性 |
| `ACTION_PRECONDITION_FAILED` | 422 | 不满足业务操作前置条件 |
| `ACTION_APPROVAL_REQUIRED` | 409 | 尚无所需审批 |
| `PROJECTION_PENDING` | 202 或业务状态 | 图谱更新仍在异步同步 |
| `GRAPH_QUERY_BUDGET_EXCEEDED` | 422 | 查询超预算 |
| `SOURCE_BATCH_CONFLICT` | 409 | 幂等键对应的内容不一致 |

## 10.4 分页、批量和同步状态

- 列表采用 cursor-based pagination；每页默认 50，最大 200。
- 图谱默认 `depth=1`、`node_limit=100`，允许用户显式展开，不在首屏全图加载。
- 写 API 支持 `If-Match` / `expected_revision`，冲突时返回最新版本摘要。
- 大导入转作 `202 Accepted` + `job_id`；保留可查询状态、明细和可重放策略。
- `projection_watermark` 显示 RDF 与 PG 一致性水位；读到旧值时界面可提示“正在同步”，不能伪装即时一致。

## 10.5 SDK 和开发者体验

- FastAPI OpenAPI → 生成 TypeScript SDK（前端）、Python SDK（IoT/情报适配）。
- 对核心接口生成独立的契约测试；模型和后端实现任何变化先改契约并更新版本。
- 所有本体引用统一使用 IRI + entity_id，而不是混用中文名字与短码。
- 前端通用 API 客户端拦截 401/403/409/422 并输出可解释的错误，而不只是 toast“请求失败”。

---

# 11. Action Engine：从知识到可执行业务

## 11.1 为什么必须独立设计

`SetGateOpening`、`AcknowledgeAlarm`、`CreateInspectionTask` 不应该是前端直接修改属性的按钮。Action 需要**动作定义、参数校验、角色权限、前置条件、审批、执行器、回执、审计、重试和幂等性**。

V0 不实现真实物理控制；P2 提供模拟 Action Engine 及可替换设备适配器，待现场系统许可与安全审核完成后再考虑真实对接。

## 11.2 Action 生命周期

```mermaid
stateDiagram-v2
  [*] --> DRAFT: 用户提出执行请求
  DRAFT --> REJECTED: 参数/权限/规则不合法
  DRAFT --> VALIDATED: 通过预检
  VALIDATED --> WAITING_APPROVAL: 需要审批
  VALIDATED --> QUEUED: 无需审批
  WAITING_APPROVAL --> REJECTED: 审批拒绝
  WAITING_APPROVAL --> QUEUED: 审批通过
  QUEUED --> RUNNING
  RUNNING --> SUCCEEDED: 获取最终有效回执
  RUNNING --> FAILED: 错误/超时
  RUNNING --> UNKNOWN: 通道异常无法确认结果
  UNKNOWN --> RECONCILING: 查询设备实际状态
  RECONCILING --> SUCCEEDED
  RECONCILING --> FAILED
  REJECTED --> [*]
  FAILED --> [*]
  SUCCEEDED --> [*]
```

真实设备动作尤其要区分：**请求已接受**、**网关已接收**、**指令下发**、**传感器/PLC 已确认现场状态**。网络超时不等于动作失败，禁止盲目重发可能产生危险的控制命令。

## 11.3 Action 定义 JSON（示例：模拟闸门开度）

```json
{
  "action_key": "SetGateOpening",
  "version": "0.1.0",
  "display_name": "调整闸门开度（演示）",
  "target_class_iri": "https://id.example.org/lzo/ontology/irrigation#WaterGate",
  "mode": "simulation_only",
  "parameters": {
    "gate_entity_id": {"type": "uuid", "required": true},
    "target_opening_pct": {"type": "number", "minimum": 0, "maximum": 100},
    "reason": {"type": "string", "min_length": 5}
  },
  "required_roles": ["operator"],
  "approval_policy": "dual_approval_if_configured",
  "preconditions": [
    "gate.status == 'online'",
    "gate.maintenance_lock != true",
    "gate.control_owner == 'device_service'"
  ],
  "executor": "simulated_water_gate_adapter",
  "side_effect_policy": "no_physical_io",
  "audit": true,
  "timeout_seconds": 30
}
```

`preconditions` 示例是**领域规则表达式**，不应在运行时通过 `eval()` 或任意 Python 代码执行。实现时用结构化 JSON AST / 规则 DSL + 白名单操作符、类型检查与单元测试；涉及现场安全的最终联锁必须由设备侧实现，不能仅相信本体规则引擎。

## 11.4 前期建议的四个 Action

| Action | 作用 | 状态权威方 | MVP 行为 |
|---|---|---|---|
| `CreateInspectionTask` | 对水塘/设备新建巡检任务 | LZO 平台 | 可实际提交到本地任务表 |
| `AcknowledgeAlarm` | 确认告警并记录操作者 | LZO 或告警服务 | 经权限校验后修改状态 |
| `AssignDeviceToPond` | 将设备监测关系绑定到水塘 | LZO Link Service（若外部系统拥有该字段则走写回） | 创建/更新受控关系并审计 |
| `SetGateOpening` | 设置闸门开度 | 现场设备平台 | **只模拟**；真机一律禁用 |

## 11.5 统一 Action 请求响应

```json
{
  "request": {
    "action_key": "CreateInspectionTask",
    "target_entity_id": "pond-uuid",
    "params": {"due_at": "2026-10-22T09:00:00+08:00", "reason": "例行巡检"},
    "idempotency_key": "e7d4b30d-..."
  },
  "response": {
    "execution_id": "exec-uuid",
    "status": "queued",
    "audit_event_id": "audit-uuid",
    "readback_required": false
  }
}
```

MVP 不必把所有 CRUD 都改造成复杂审批 Action，但所有具有明确业务语义的**状态变更**都应经过统一 Action Gate，普通 Schema/对象编辑走受控 CRUD 同样必须鉴权、校验、审计。

## 11.6 幂等与外部副作用

- 每个执行请求由服务端记录 `execution_id`，重复幂等键返回同一执行结果。
- 事务 Outbox 保证至少一次投递；外部执行器必须幂等或有回执去重策略。
- 不存在无成本的“跨数据库 + 真实设备”的全局 ACID；应使用 saga、查询实际状态与必要人工补偿。
- 高风险动作强制审批/人工确认、操作时间窗口和二次校验，并默认关闭真实设备适配器。

---

# 12. 前端产品设计与独立视觉规范

## 12.1 产品定位

前端暂定名：**「湖域 · LZO Workbench」**。正式名称可以调整，但必须与 Semantica / WorldWideView / 旧梁子湖系统的 UI 有明显品牌区分。

前端不是普通后台数据表，也不是只有一张知识图谱，更不是世界地图的缩小版。它是兼顾**本体设计者、农业运营人员、设备与数据工程师**的统一农业知识操作台。

### 三种明确的使用情境

- **设计模式（Design）**：管理类、属性、约束、命名空间、版本、依赖和发布。
- **运营模式（Operate）**：管理水塘/农田/设备实例、关系、状态、操作和证据。
- **空间模式（Explore）**：在区域地图、关系图谱、时间轴之间切换，观察同一对象的不同视角。

三种模式不是三个割裂的系统：**同一个 Entity ID** 能从类树、实例卡片、地图标记、关系网络、证据面板互相跳转。

## 12.2 视觉设计方向：以农业和湖域为核心

结合 WorldWideView 的空间沉浸体验与 Semantica 的图谱工作台，形成独有的“**湖蓝 + 稻绿 + 深海石墨**”界面语言。

| Token | 推荐颜色 | 用途 |
|---|---|---|
| `--lzo-bg` | `#0B1620` | 深石墨蓝背景（暗色主题） |
| `--lzo-surface` | `#142631` | 一级面板 |
| `--lzo-raised` | `#1B3440` | 卡片/高亮面板 |
| `--lzo-lake` | `#2AA8B8` | 主交互与地理关联 |
| `--lzo-rice` | `#8AC48B` | 农业类/田块类 |
| `--lzo-amber` | `#F2B96B` | 候选事实、待处理、提示 |
| `--lzo-critical` | `#EF7373` | 危险/拒绝/严重告警 |
| `--lzo-text` | `#EBF4F3` | 主文字 |
| `--lzo-muted` | `#94AEB6` | 次级文字 |
| `--lzo-border` | `#2B4753` | 分割线/边框 |

**色彩不是唯一信息通道**。告警、候选、已审核必须配合图标和文字；浅色模式在 V1 后期可补充，但基础 token 应同时考虑高对比度和可访问性。

### 视觉特色

1. **地图、水体、农田有明确视觉编码**：湖水系蓝绿、农田稻绿、水工设施灰蓝、告警琥珀/红。
2. **不是炫酷科幻风**：避免全屏装饰性网格、复杂粒子、过多霓虹边缘；GIS 数据本身成为主角。
3. **工程信息密度适中**：左导航 224–256px、详情抽屉 360–460px、顶部工具栏约 56–64px，支持折叠，视窗尺寸变化时合理退化。
4. **细腻但不过度的动画**：画布拖动、选中高亮、关系展开使用 120–220ms 过渡；数据量大时优先帧率而非特效。
5. **中文优先 + 英文术语可见**：例如标题“水质传感器”，辅助显示 `WaterQualitySensor` 和 IRI，而不是满屏原始代码。
6. **突出证据与状态**：每个对象和关系可以查看来源、核验状态、更新时间、关联范围。
7. **明确开发/演示标识**：合成数据始终显示“演示数据”；不误导现场用户。

## 12.3 应用导航体系（推荐）

| 一级页面 | 路由 | 核心功能 |
|---|---|---|
| 总览 Overview | `/overview` | 本体版本、对象数量、健康状态、近期变更、空间统计 |
| 本体设计 Ontology Studio | `/ontology/studio` | 类树、画布、属性/关系编辑、继承预览、发布 |
| 实体管理 Object Registry | `/objects` | 实例表格、筛选、动态表单、批量操作、历史 |
| 关系探索 Knowledge Graph | `/graph` | 实体邻域、路径、图例、状态/时间过滤、SPARQL 入口 |
| 梁子湖地图 Geo Workspace | `/geo` | 2D 区域地图、图层管理、空间选取、对象详情 |
| 规则与校验 Quality Lab | `/quality` | SHACL、冲突、约束错误、待审核候选、数据质量 |
| 数据源管理 Sources | `/sources` | 数据连接、映射、导入预检、失败回执、同步状态 |
| 动作中心 Actions | `/actions` | Action Type 目录、模拟操作、审批、执行历史 |
| 版本治理 Governance | `/governance` | Schema 版本 diff、审核、回滚计划、审计 |
| 设置 Settings | `/settings` | 用户、角色、系统配置、配色、命名空间、插件 |

**导航取舍**：不要求 V0 所有页面都有完整功能；未实施模块展示产品路线说明或隐藏，不允许用假按钮和静态占位数据冒充已实现。

## 12.4 桌面端全局骨架（文字线框）

```text
┌─────────────────────────────────────────────────────────────────────────────────────────┐
│  湖域 LZO    梁子湖农业本体平台                [全局查找对象/类/关系]   [版本] [用户]   │
├──────────────┬──────────────────────────────────────────────────────────────────────────┤
│ 总览         │ 面包屑  /  当前工作模式                         [保存] [校验] [发布]   │
│ 本体设计     ├──────────────────────────────────────────────────────────────────────────┤
│ 实体管理     │                                                                          │
│ 关系探索     │                     主要工作区域 / 地图 / 节点编辑器                     │
│ 梁子湖地图   │                                                                          │
│ 规则校验     │                                                                          │
│ 数据源       │                                                                          │
│ 动作中心     │                                                                          │
│ 版本治理     ├──────────────────────────────────────────────────────────────────────────┤
│ 设置         │ 底部状态：选中对象 · 数据性质 · 图谱版本 · 同步状态 · 最近更新时间       │
└──────────────┴──────────────────────────────────────────────────────────────────────────┘
```

**整体页面滚动原则**：工作台采用 `height: 100dvh`，左侧导航及顶部栏固定，主内容的列表/画布/右侧属性面板分别滚动。全局不应因为中部图谱滚轮而让整个浏览器页面上下漂移；小屏幕允许切换为纵向布局。

## 12.5 本体设计器 Ontology Studio（最重要）

```text
┌──────────────────────────────────────────────────────────────────────────────────────────┐
│ Ontology Studio  [包: Core ▼] [版本: Draft ▼]  [校验] [对比版本] [提交发布]              │
├─────────────────────┬────────────────────────────────────────┬──────────────────────────┤
│ 类 / 属性 / 关系     │              本体可视化画布             │ Inspector · 当前选择       │
│ [搜索]              │                                        │                          │
│ ▾ AgriculturalEntity│     ┌────────────┐                     │ WaterQualitySensor       │
│   ▾ Zone            │     │ Device     │                     │ IRI / Version / Parent   │
│       Field         │     └─────┬──────┘                     │ [名称] [描述]            │
│       Pond          │           │ subclass                   │ 继承属性                 │
│   ▾ Device          │     ┌─────▼──────┐                     │ 属性定义                 │
│       Sensor        │     │ Sensor     │────monitors────┐     │ SHACL 约束               │
│       WaterQuality  │     └─────┬──────┘                 │     │ 修改历史                 │
│   ▾ Action Types    │           ▼                        ▼     │ [添加属性] [添加父类]     │
│ [+ 新建类]          │     WaterQualitySensor           Pond   │                          │
├─────────────────────┴────────────────────────────────────────┴──────────────────────────┤
│ 变更预览：新增 1 类 · 继承 3 属性 · 添加 1 关系 · 当前 SHACL 校验已通过                   │
└──────────────────────────────────────────────────────────────────────────────────────────┘
```

### 设计器交互细则

- 左栏支持类树层级、多条件过滤、被引用计数、不同本体包切换、收藏、类型搜索。
- 中间画布是**Schema 图**，主要显示 Class、Property、SubClass、Domain/Range，不与实例图混淆。
- 右侧 Inspector 显示当前类的中文/英文标签、稳定 IRI、父类列表、继承属性、直接属性、关系端点、SHACL Shape 和版本历史。
- “新建类”默认生成未发布草稿；用户需指定父类、label、namespace、description；IRI 发布后不可轻易更名。
- “添加属性”允许数据类型、单位、必填、最小最大、枚举、属性所有权、表单组件类型；将 UI 配置与 SHACL 约束分开保存。
- 线条颜色和形状区分 `subClassOf` 与对象关系；画布不超过 50–100 节点时全图显示，复杂时按层级/子树展开。
- 拖拽连接时先预览关系类型与方向，确认后写入草稿；不允许画了线就直接污染生产本体。
- 页面提供“继承属性预览”与“受影响实例数量”，防止修改上级类后意外让下级实例全部变为非法。
- 草稿编辑必须具备未保存提示、撤销/重做、自动保存和并发编辑冲突提示。

## 12.6 实体管理 Object Registry

- 首页包含**搜索、类型过滤、数据性质过滤、审核状态、来源系统过滤、空间区域过滤**。
- 列表的列根据 Object Type 自动生成，并能保存“视图定义”；避免一个固定 `name/type/status` 表格覆盖所有农业对象。
- 点击某行打开详情抽屉，提供 `Overview`、`Properties`、`Links`、`Geometry`、`Observations`、`Provenance`、`History`、`Actions` tabs。
- “创建对象”先选择 Schema 已发布的类，依据有效的 SHACL/字段元信息生成表单；普通文本、数字、日期、枚举、单位、对象引用和地理对象选择自动渲染。
- 编辑时显示字段权属：本体可编辑、来源只读、派生只读；特殊业务操作显示 Action 按钮。
- 停用时先给出影响报告：哪些关系、地图标记、下游业务会受影响；不允许静默硬删除。

## 12.7 知识图谱 Graph Explorer

Semantica 的图形浏览机制可参考，但交互上聚焦“农业对象为什么彼此关联”。

- 实体节点色彩按农业语义分类：Pond（水蓝）、Field（稻绿）、Sensor（青绿）、Batch（金色）、Evidence（灰色）、Alarm（红色）。
- 边上必须显示关系名称和方向，且候选边为虚线或虚边框，已采纳边为实线。
- 左侧过滤：对象类、关系类型、审核状态、有效时间、来源系统。
- 中间画布：缩放、局部展开、路径查找、关系高亮、聚类折叠、适量自动布局。
- 右侧 Inspector：点击节点显示对象信息及“查看地图位置 / 查看原始证据 / 查看同类对象”。
- 图过大时提供结果提示“已展示 100 / 共 N 个匹配对象”，不可卡死浏览器。
- 明确“图谱结果不自动证明因果或地理包含”，关系来源必须可点击。

## 12.8 梁子湖地图 Geo Workspace

这是 WorldWideView 的启发来源，但必须**以农业区域地图为中心**，不是直接复制全球 3D Globe 导航。

```text
┌────────────────────────────────────────────────────────────────────────────────────────┐
│ 梁子湖地图   [农田][水塘][设备][水利][告警]    [2D] [3D(后续)]  [时间] [筛选]          │
├─────────────────────┬──────────────────────────────────────────────┬──────────────────┤
│ 区域图层            │                                              │ 对象详情         │
│ ☑ 农田              │          梁子湖行政区界（核验后）            │ POND-001         │
│ ☑ 水塘              │       水塘与田块 Polygon                     │ 类型/面积/状态   │
│ ☑ 水质测点          │       传感器/摄像头/闸门 Marker                │ 关联传感器       │
│ □ 摄像头            │                                              │ 最近观测摘要     │
│ □ 灌溉网络          │          ← 地图是主要工作区 →               │ 来源证据         │
│ □ 任务/告警         │                                              │ [打开知识图谱]   │
│ [区域搜索]          │                                              │ [创建巡检任务]   │
├─────────────────────┴──────────────────────────────────────────────┴──────────────────┤
│ 时间轴  ◀ [----------------------●------------------] ▶  采样时间 / 告警回放       │
└────────────────────────────────────────────────────────────────────────────────────────┘
```

### 地图行为

- 地图启动默认 zoom 至梁子湖区的**可信行政边界 bbox**，未获得真实边界前使用演示 GeoJSON，并明确标注。
- 图层控制可展开类别与数量；过滤后主地图及右侧对象清单一起更新。
- 点击 `POND-001` 显示对象详情；“查看本体”跳转 Ontology Studio 中该类；“查看关系”跳转 Graph Explorer 中该实例。
- 时间轴初期用于**查询历史观测/告警**，并不虚构每一秒设备动态；需要有源数据才可回放。
- 未来点击地块进入局部 3D，建筑/设施三维模型只作为 GIS 资源引用，不能充当真实测量数据。
- 坐标、比例尺、加载状态、更新时间和数据性质固定显示；不抄袭 WWV 现有仪表盘控件。

## 12.9 响应式和无障碍要求

- `>=1280px`：三栏工作台；`768–1279px`：收窄左侧导航、右详情抽屉覆盖；`<768px`：手机端主要支持查看、检索和轻量操作，不在手机上强求复杂拖拽建模。
- React Flow 编辑器必须提供键盘/列表替代操作，不能只有鼠标拖线才能定义关系。
- 支持键盘 Tab 导航、可见焦点、ARIA 标签、适当对比度；状态变化通过文字提示。
- 大图谱虚拟化/渐进加载；编辑界面中全局搜索需要快捷键（如 Ctrl/Cmd+K）。
- 降级态必须完善：RDF 不可用时查询显示错误与重试，不显示虚假成功；地图服务不可用时可用对象列表替代。

## 12.10 UI 不能复制的具体界限

**明确禁止**：从 `liangzihu-ontology-traceability/implementation/web` 复制现有 DOM/CSS/JS、图标、布局和页面截图；从 Semantica 或 WorldWideView 直接截取组件贴图作为自己的 UI；未获许可使用第三方的品牌标识与地图素材。

**允许借鉴**：Semantica 的图谱探索、草稿管理、类树/Inspector 模式；WorldWideView 的地图主视图、图层控制、搜索、时间轴；React Flow/MapLibre 等开源基础组件的合规使用。

**LZO 独特性**：湖水与农田分区语义视觉；“概念—事实—操作”三模式；一键图谱/地图交叉定位；现场设备及证据状态设计；中国农业区划与业务术语；本体版本和数据性质可见性。

---

# 13. 页面级交互流程与前后端联动

## 13.1 场景 A：定义子类并生成可编辑实例表单

```mermaid
sequenceDiagram
  actor User as 本体管理员
  participant UI as React Ontology Studio
  participant API as FastAPI Schema API
  participant VAL as SHACL/RDFS Validator
  participant PG as PostgreSQL Registry
  participant RDF as RDF Graph Store
  User->>UI: Sensor 下新增 WaterQualitySensor
  UI->>API: POST /ontology/drafts + draft patch
  API->>PG: 持久保存草稿与 revision
  UI->>API: POST /drafts/{id}/validate
  API->>VAL: 结构、继承、形状、兼容性检查
  VAL-->>API: 报告/继承属性清单
  API-->>UI: 预览受影响字段和实例
  User->>UI: 审核并发布
  UI->>API: POST /ontology/releases
  API->>PG: 版本状态、审计、签名摘要
  API->>RDF: 安装已校验 OWL / SHACL 命名图
  API-->>UI: release_id / effective_version
  UI->>API: GET /classes/.../form-schema
  API-->>UI: 字段、单位、继承属性、控件元数据
  UI-->>User: 新建传感器动态表单
```

注：以上序列图中 PG 和 RDF 的发布需经过**可恢复的发布事务状态机**，跨数据库不是原子提交。对用户只有完成安装并确认图谱可读后才能报告最终“发布完成”；半途失败则标记 `publishing_failed` 并可补偿/重试。

## 13.2 场景 B：从地图到图谱再到具体对象

1. 用户进入 `/geo`，选中“水塘”图层。
2. MapLibre 请求 `/spatial/zones?bbox=...&kind=pond` 返回 GeoJSON 与统一 `entity_id`。
3. 用户点击水塘 `POND-001`，右侧 Inspector 调用 `/objects/{id}`。
4. Inspector 加载 `/objects/{id}/links` 获取 `monitors`、`locatedIn` 等关系，默认只包含 `accepted`。
5. 点击关联传感器，再通过 `/telemetry/latest?entityId=...` 获取最新测量摘要；记录采样时间与质量标记。
6. 点击“查看关系网络”跳转 `/graph?focus={entity_id}`，关系图以该水塘为中心展开；不重新创建第二套实例 ID。
7. 用户可查看来源记录、审核状态，必要时进入受控的编辑或巡检 Action。

## 13.3 场景 C：不合法关系的拒绝

输入：`Sensor-001 dev:monitors Shipment-001`，目标类型为 `Shipment` 而非允许的 `Zone/Pond`。

预期：前端在选择框提前过滤，但服务端仍执行 SHACL/端点规则；返回 `422 LINK_ENDPOINT_INCOMPATIBLE`，附带允许目标类型、实际目标类型与可读错误；无数据库写入、无幽灵边、无图谱异步投影。

## 13.4 场景 D：草稿发布与历史数据的兼容

在 `Sensor` 级增加强制字段会影响所有子类。发布前必须报告旧实例缺失字段数量，提供“补数据 / 放宽新类约束 / 采用宽限期”方案。禁止管理员点击发布后原有大量实体瞬间被不透明地判为无效。

---
# 14. GIS、IoT、情报和溯源模块的接入

## 14.1 与整个梁子湖数字孪生总平台的关系

**LZO 是共享的业务对象/语义核心，不是替代全部业务系统**。数字孪生总平台负责总导航、地图/3D、设备应用、农业情报、生产溯源等业务入口；它们通过统一对象 ID 和 Ontology API 获得一致的对象定义与关联。

```mermaid
flowchart LR
  TWIN[数字孪生前端] --> OAPI[Ontology API]
  DEVICE[设备/传感器子系统] --> OAPI
  INTEL[外部情报/内部信息子系统] --> OAPI
  TRACE[溯源子系统] --> OAPI
  OAPI --> MODEL[统一农业对象 / Link / Actions]
  MODEL --> DATA[PG + RDF + GIS + Time Series]
```

“共享本体”不意味着所有子系统必须读取同一张数据库表。强制统一的是**身份、类型语义、关系定义、访问契约和变更事件**。

## 14.2 地图：2D → 分区 3D → 设备联动

- P1：梁子湖区行政边界、田块/水塘真实/合成 Polygon、设备标注点；点击显示对象详情。
- P2：一个真实或者合成的示范分区实现局部 3D；3D 对象与 `entity_id` 一一绑定。
- P2：设备卡片显示“监测什么、作用于哪个分区、最近状态、历史曲线、原始证据”。
- P3：跨分区分析及时间重播，由 GIS/时序服务提供，不强行让 SPARQL 负责视频、几何全部细节。
- 设备在线状态有 TTL；前端不能把数小时之前的旧数据仍标记为“实时”。

## 14.3 IoT 设备：设备拓扑和观测分离

**本体图存**：设备身份、设备类别、归属地块、监测指标、测量单位、控制权限、传感器/执行器之间的拓扑关系。

**时序/网关存**：每次测量值、原始通讯消息、摄像头视频流、设备真实执行回读。摄像头视频默认保留访问 URI/权限信息，知识图谱不包含视频二进制。

**数据桥接**：设备消息 → 解码/单位归一化 → device_id 与 LZO entity_id 映射 → 写时序表 → 最近观测摘要 → 有必要时更新 RDF 语义事实。错误设备 ID、未知单位、异常时间戳进入隔离队列，不自动推送“已确认”值。

## 14.4 情报系统（外部/内部）

农业情报模块可先独立实现“外部信源采集 → 去重 → 文档解析 → 摘要 → 简报”，**不要求等待完整 Ontology 才能开发**。之后通过语义适配层实现：

```text
政策/论文/新闻/内部报告
        ↓
文档与来源记录（SourceDocument）
        ↓ 可选 LLM/NER 抽取
候选实体 / Candidate Claim
        ↓ 类型映射 + 证据 + 人工复核
已采纳的 appliesTo / concerns / supports
        ↓
某农田/水塘/作物对象的“相关政策与知识”面板
```

外部文档抽取必须保留 `model_version`、`source_hash`、`extraction_timestamp`、文本片段位置或可回溯证据 URI、`confidence`（仅作为建议分数，不等于事实概率）。高风险建议（施药、用药、水质达标等）不得直接自动控制设备或生成合规证明。

## 14.5 溯源与批次

溯源可以复用统一本体 `ProductionBatch`、`MaterialLot`、`Shipment`、`QualityCheck`。批次与地块、水塘、农事活动、质量检测建立真实、有证据的关系。只呈现已审核可公开内容；来源关系不自动公开，公开端需专门的脱敏、审核和撤回机制。

## 14.6 AI Agent 的未来位置

AI Agent 只调用同一 Ontology API/Action API，并遵守权限与审核；不持有 RDF Store 的裸 SPARQL UPDATE 权限和 IoT 网关的生产控制密钥。允许其：查询对象、解释关系、提出候选知识、生成待审核操作计划；真实设备控制仍要经过独立授权及安全联锁。

---

# 15. 安全、审计、版本、迁移与运维

## 15.1 权限模型（最少四类角色）

| 角色 | 看什么 | 可以改什么 | 禁止什么 |
|---|---|---|---|
| Viewer | 授权范围内的对象/图谱/地图 | 无 | 直接更改数据和操作设备 |
| Data Operator | 所属空间和设备数据、业务表单 | 受权本体自有属性、申请巡检 | 发布本体、直接修改来源权威属性 |
| Ontology Editor | Schema 草稿和测试图 | 创建类、属性、关系草稿 | 单独审批自己的高风险发布（正式环境） |
| Ontology Admin / Reviewer | Schema 发布、审核、系统健康 | 审批发布、授权、数据治理 | 无审计修改历史、绕过设备联锁 |

正式环境必须从 RBAC 向**对象级授权**演进：按区域、农场/部门、数据敏感级别、操作类型控制。对任何图查询，必须在服务端执行对象级过滤；不能只在前端隐藏不允许看的节点。关系图也不能泄露无权对象名称、关联数量或位置信息。

## 15.2 认证、网络与密钥

- 使用 OIDC；Access Token 只走 HTTPS；后端根据签名、audience、issuer、expiration 校验。
- 数据库账户最小权限分离：API 用户、迁移用户、同步用户、备份用户；RDF 写权限只授予受控服务。
- 禁止公开图存储管理端口和 SPARQL UPDATE 端点。
- 敏感配置使用环境变量/Secret Manager，绝不提交到 Git；准备 `.env.example`，使用随机开发密钥。
- 数据库备份加密、回收策略、日志脱敏、请求大小/频率限制、文件上传类型和大小控制。
- 读取外部 RDF/URL 时防 SSRF、重定向到内网、循环引用、大文件攻击；限制 Schema graph 三元组数量和 SHACL 执行时间。

## 15.3 操作审计

审计事件至少包括：操作者、角色、租户/区域、请求来源、实体 IRI、动作/修改类型、修改前后摘要、原因、Schema 版本、成功与否、correlation ID、时间、外部回执 ID。审计日志原则上不可由普通业务 API 修改。

对本体定义发布、候选事实审核、Action 执行（包括拒绝）都做审计；必要时以 hash chain / append-only 审计存储增强防篡改能力。

## 15.4 本体版本变更规范

- **Patch**：标签、描述修正，不改变语义含义与数据契约。
- **Minor**：新增兼容类、属性、关系、可选 Shape；旧客户端继续兼容。
- **Major**：删除/更名 IRI、改变父类、强制字段、改变业务语义和权限模型等，必须迁移规划。
- 已发布 IRI 禁止“复用旧名字但改变语义”；采用 `owl:deprecated true` + 替代映射与迁移周期。
- Schema 发布前输出 impact report：受影响实例数、Shape 违规数、客户端使用位置、表单变化、数据映射兼容性、回滚策略。
- 发布状态机：`draft → under_review → approved → publishing → published | publishing_failed`；失败可重试，拒绝采用半成功状态。

## 15.5 备份与灾难恢复

备份应作为**可恢复系统组合**，不仅保存 `.db` 文件：

1. PostgreSQL 一致性快照（含模式版本、实例、关系、审计、Action/outbox）。
2. RDF Store 标准导出（TriG/N-Quads）和发布的 OWL/SHACL 版本。
3. MinIO/S3 对象与版本、checksum、证据目录。
4. GIS/时序数据备份或原系统可恢复性证明。
5. 配置 manifest、依赖锁文件、密钥恢复流程、提交 SHA、迁移清单。

建议开发环境每周做一次恢复演练；正式环境确定 RPO/RTO（示例目标可设 RPO≤24h、RTO≤4h，必须通过演练验证后才成为 SLA）。不要把“备份成功”等同“完整恢复验证通过”。

## 15.6 监控指标

- API：延迟 P50/P95/P99、4xx/5xx、错误码分布。
- RDF：查询超时、图谱三元组数量、推理任务耗时、Named Graph 版本。
- 一致性：outbox 积压、投影延迟、失败重试/死信数。
- 数据质量：SHACL 违规数量、候选待审核数量、来源映射失败率。
- GIS：地图图层请求耗时、几何错误、坐标异常。
- 动作：提交/拒绝/审批/成功/未知/超时数，外部回执延迟。
- 安全：非法查询、越权访问、Schema 变更、生产设备动作尝试。

---

# 16. 工程目录、环境和本地启动方案

## 16.1 建议全新 Git 仓库与 Monorepo 结构

**强烈建议新建仓库**，例如 `liangzihu-ontology-platform`，与旧 `liangzihu-ontology-traceability` 保持独立，避免被旧前端和数据结构绑住。

```text
liangzihu-ontology-platform/
├── README.md
├── ARCHITECTURE.md                 # 本方案的精简版和架构决策链接
├── CONTRIBUTING.md
├── SECURITY.md
├── LICENSE
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile
├── docs/
│   ├── ADR/                       # Architecture Decision Records
│   ├── api/                       # OpenAPI 文件及示例
│   ├── design/                    # 前端 UI 设计与交互规范
│   ├── runbooks/                  # 备份/恢复/升级/故障排查
│   └── test-scenarios/            # 验收场景
├── ontology/
│   ├── catalog.yaml
│   ├── foundation/1.0.0/
│   ├── core/1.0.0/
│   ├── aquaculture/1.0.0/
│   ├── irrigation/1.0.0/
│   ├── shapes/
│   └── examples/
├── apps/
│   └── web/                       # 全新 React+TS 工作台（禁止复制旧版）
│       ├── src/
│       │   ├── app/               # 路由、应用壳、鉴权
│       │   ├── features/
│       │   │   ├── ontology-studio/
│       │   │   ├── object-registry/
│       │   │   ├── graph-explorer/
│       │   │   ├── geo-workspace/
│       │   │   ├── quality-lab/
│       │   │   ├── sources/
│       │   │   ├── actions/
│       │   │   └── governance/
│       │   ├── components/        # 自有设计系统
│       │   ├── design-tokens/
│       │   ├── sdk/               # OpenAPI 自动生成类型/请求封装
│       │   └── styles/
│       ├── tests/
│       └── package.json
├── services/
│   └── api/
│       ├── pyproject.toml
│       ├── app/
│       │   ├── main.py
│       │   ├── core/              # 配置、认证、异常、日志
│       │   ├── domains/
│       │   │   ├── ontology/
│       │   │   ├── objects/
│       │   │   ├── links/
│       │   │   ├── validation/
│       │   │   ├── ingestion/
│       │   │   ├── provenance/
│       │   │   ├── geo/
│       │   │   ├── actions/
│       │   │   └── telemetry/
│       │   ├── adapters/
│       │   │   ├── semantica_adapter.py
│       │   │   ├── rdf_store.py
│       │   │   ├── postgres_repository.py
│       │   │   ├── postgis_adapter.py
│       │   │   ├── fake_device_adapter.py
│       │   │   └── external_source_adapter.py
│       │   ├── schemas/          # Pydantic / OpenAPI
│       │   ├── db/
│       │   └── workers/
│       ├── migrations/            # Alembic/SQL migrations
│       └── tests/
├── infra/
│   ├── docker/
│   ├── compose/
│   └── backups/
├── scripts/
│   ├── seed_synthetic.py
│   ├── export_ontology.py
│   ├── verify_schema.py
│   └── smoke_test.py
└── .github/workflows/
    ├── ci.yml
    └── release.yml
```

### 模块边界规则

- `domains/ontology` 不允许调用前端逻辑，也不能直接控制设备；其职责仅在语义模型/生命周期。
- `adapters/semantica_adapter.py` 是唯一依赖 Semantica 具体 SDK 的边界；其他领域模块面向自有稳定接口。
- `apps/web` 不从 `liangzihu-ontology-traceability` 拷贝代码；设计 token、SVG 图标、表单和页面独立编写。
- 后端 `schemas/`/OpenAPI 是 API 契约，前端使用 SDK 生成类型，不在两端手写两份不同定义。
- SQL migrations 必须版本化、可验证和可恢复；不能通过自动建表替代生产模式迁移管理。

## 16.2 建议的本地开发启动顺序

**P0-0：环境与仓库**

```bash
# 新建空仓库并进入目录（命名可调整）
mkdir liangzihu-ontology-platform
cd liangzihu-ontology-platform
git init

# 初始化 Python 服务和 React/Vite 工程按选定脚手架分别执行，
# 不从旧梁子湖前端复制任何文件。
```

**P0-1：基础依赖**

安装 Python 3.11/3.12、Node 20+、Docker/Compose；前后端依赖分别锁定。优先使用 `uv`（Python）和 `pnpm`（Node），避免一份未锁依赖在不同机器产生无法复现的图存储接口变化。

**P0-2：持久化容器**

先部署 PostgreSQL + PostGIS；创建独立数据库 `lzo_core`。RDF Store 初期可以使用项目本机独立目录（例如 `runtime/rdf-data`）中的持久 Oxigraph Store，再演进为独立进程/容器；**必须把 RDF 数据目录挂到非临时卷，并验证重启后数据存在**。如果选择 Fuseki，需显式配置 TDB2 数据目录和 SPARQL 读写权限。

**P0-3：最小 API**

1. FastAPI `/health/live`、`/health/ready`。
2. PostgreSQL schema migrations 执行成功。
3. RDF Store 写入一个 `Pond` 实例，进程退出后重新打开，SPARQL 能查到相同 IRI。
4. 挂载正式 OWL / SHACL 文件并通过第一条验证。
5. React 前端调用 API 显示已发布类树与 `POND-001` 实例。

**P0-4：第一次端到端 Smoke Test**

```text
1) 打开本体管理器，创建 Sensor 的子类。
2) 保存草稿、校验、正式发布，查看有效版本。
3) 新建传感器实例；表单由发布后的属性定义动态生成。
4) 新建监测关系；错误目标类被服务端拒绝。
5) 关系在实例详情和 RDF/SPARQL 查询中均可找到。
6) 重启全部服务；Schema/实体/关系/审计仍在。
```

## 16.3 配置示例（`.env.example`，不包含真实密码）

```dotenv
APP_ENV=development
API_BASE_PATH=/api/v1
DATABASE_URL=postgresql+psycopg://lzo_dev:CHANGE_ME@localhost:5432/lzo_core
RDF_BACKEND=oxigraph
RDF_DATA_DIR=./runtime/rdf-data
ONTOLOGY_BASE_IRI=https://id.example.org/lzo/
SCHEMA_REGISTRY_DIR=./ontology
OIDC_ISSUER_URL=http://localhost:8081/realms/lzo
OIDC_AUDIENCE=lzo-api
ENABLE_REAL_DEVICE_ACTIONS=false
ENABLE_SYNTHETIC_SEED=true
SHACL_TIMEOUT_SECONDS=15
SPARQL_READ_TIMEOUT_SECONDS=10
```

生产版本必须将 `CHANGE_ME` 改为 Secret 注入，不通过提交 `.env` 保存。上述 OIDC 端口和服务只是配置示例，不代表已配置/运行的身份服务。

## 16.4 强制执行的开发命令（通过 Makefile 统一封装）

建议所有模块提供这些**目标命令**，具体实现由脚手架确定：

```makefile
# 目标行为定义；不是声称现有仓库已经存在这些命令
help:                ## 命令列表
bootstrap:           ## 检查工具链、安装锁定依赖
infra-up:            ## 启动数据库及开发依赖
infra-down:          ## 停止依赖，不删数据卷
migrate:             ## 执行迁移
ontology-validate:   ## 检查 OWL/Turtle、SHACL、命名空间与依赖
seed-synthetic:      ## 导入清晰标识的示例数据
dev-api:             ## 启动 FastAPI
dev-web:             ## 启动 React Vite
test:                ## Python + TypeScript + 合约测试
test-integration:    ## 使用真实持久 PG/RDF 的集成测试
smoke:               ## UI + API 主流程检查
backup:              ## 创建可验证的备份
restore-check:       ## 在隔离目录演练恢复
```

这些 target 要在 P0 完成时实际创建并运行，不能仅作为空命令标榜完成。

## 16.5 Git 协作规范

- `main` 始终可部署/可运行；新功能走 `feature/*` → Pull Request → CI → Review → Squash Merge。
- 每个 PR 包含：业务目标、API/Schema 变化、迁移/回滚影响、测试、UI 截图（适用时）。
- OWL/SHACL 文件与数据库迁移必须经过专门 CODEOWNERS 评审。
- 强制最少一人 Review（实际团队只有一位审核人也可以），禁止未经过审查的破坏性 Schema 变更。
- 软件版本、Ontology 包版本和数据库迁移号分别维护；记录兼容矩阵。

---

# 17. 分阶段开发路线与验收标准

> 以下以约 2–4 名开发者 + Coding Agent 协作的**参考顺序**给出。具体工期取决于人员和设备数据获取，不保证自然周内全部完成。任何阶段都需要达到验收门槛再进入下一个阶段。

## P0（基础与“真本体”最小闭环，建议 2–4 周）

**交付**：独立前端脚手架、FastAPI API、PostgreSQL/PostGIS、持久 RDF Store、正式 Turtle/SHACL、实体和关系 CRUD、第一版动态表单。

| 任务 ID | 可执行任务 | 可证明的输出 | 验收方式 |
|---|---|---|---|
| P0-01 | 新建独立仓库与 CI、目录规范 | 可重复环境初始化 | 空环境重建成功 |
| P0-02 | 安装 PG/PostGIS + RDF Store | 两种数据库均持久化 | 重启后数据不消失 |
| P0-03 | 创建 10–20 个基础农业类及关系 | `.ttl` + 命名空间 + 文档 | 语法/无环层级检查通过 |
| P0-04 | 建立 OWL/RDFS 子类测试 | `Sensor → WaterQualitySensor` 推理用例 | 查询子类实例可得正确结果 |
| P0-05 | 建立 SHACL 校验 | 有效/无效实例 Turtle | 违规产生机器可读报告 |
| P0-06 | Schema 草稿/发布和 Git 发布清单 | 有发布 API、持久版本 | 发布后重启仍可读取版本 |
| P0-07 | Object/Link CRUD | API + 审计 + 乐观锁 | 重复写、错误端点、并发冲突都正确处理 |
| P0-08 | 新 UI：本体类树 + Inspector + 简化画布 | React 页面 | 不引用旧前端；新类可显示 |
| P0-09 | 表单 Schema 和实例创建 | 动态字段渲染 | 新类创建后无需新写 CRUD 页面 |
| P0-10 | 合成数据与 Smoke 流程 | 1 农田、1 水塘、2 设备等 | `POND-001 ← monitors ← SENSOR-001` 闭环 |

**P0 Gate**：验证“**新增子类—发布—创建实例—关系校验—SPARQL 查询—重启不丢数据**”。未做到此流程就不能称为 Ontology Platform 内核完成。

## P1（可用农业本体工作台，建议后续 3–6 周）

| 任务 | 内容 | 验收 |
|---|---|---|
| P1-01 | Ontology Studio 完整属性、关系、版本编辑 | 类树+画布+Inspector 联动，草稿可恢复 |
| P1-02 | SHACL Studio / Quality Lab | 违规列表、定位实例、重跑校验 |
| P1-03 | Graph Explorer | 可按节点邻域、关系、时间、审核状态检索 |
| P1-04 | PostGIS Geo Workspace | 梁子湖示例分区/设备，地图和图谱交叉定位 |
| P1-05 | Sources + source-binding | 选 1 个领域来源表做只读小样本预检与导入 |
| P1-06 | Schema diff/发布审核/弃用策略 | 类/属性变更前可看到受影响实例 |
| P1-07 | 身份授权与对象级控制基础 | 不能越权查询/修改非授权实体 |
| P1-08 | 系统健康、备份和恢复 | PG/RDF 组合恢复演练成功 |

**P1 Gate**：运营用户在前端添加一类农业实体，录入真实或明确标注的演示实例，和 GIS/设备实体建立关系；其他页面自动查询到该类和实例；能解释每条关系来源。

## P2（Operational Actions，建议后续 3–5 周）

- Action Type 定义、参数和业务规则 DSL。
- `CreateInspectionTask`、`AcknowledgeAlarm`、`AssignDeviceToPond` 实际业务行为。
- `SetGateOpening` 仅模拟，提供显式 `SIMULATION ONLY` 视觉提示与安全检查。
- 动作状态机、权限、审批、outbox、幂等、日志、失败恢复。
- 总平台设备管理/农业运营页面通过统一 Action API 执行。

**P2 Gate**：不同前端入口和 API 客户端调用同一 Action，得到一致的权限与业务校验、相同审计；提交重复请求不重复执行；模拟器宕机有可恢复状态，不凭空返回“现场执行成功”。

## P3（数据扩张与真实业务对接，长期）

- 多个农场、地块、水体的真实数据按授权分批接入。
- 情报系统的候选事实、政策/论文实体关联。
- 生产批次与物料谱系、可审计溯源。
- 设备真实历史观测与告警联动（初期只读）；可靠实时通信和回执。
- 高级空间查询、多本体包依赖、SKOS 词表治理、导出 RDF/JSON-LD。
- 依据负载情况分离 RDF 服务、worker、数据库集群和对象存储。

**P3 Gate**：在生产前必须满足数据授权、真实性、安全审计、回滚、负载和真实设备联调验收；与系统上线验收分开进行。

## 17.1 第一阶段 Backlog 优先顺序

建议 issue 最初顺序：

```text
01. 建立 repo/CI/开源许可清单
02. FastAPI + PG + RDF 持久化环境
03. ontology/core.ttl + shapes.ttl + 语义测试
04. Schema Release 模型与只读 API
05. Schema 草稿管理、发布流程
06. Object/Link API 与关系校验
07. 前端设计 token / 路由框架（全新设计）
08. Ontology Studio 类树 + 继承预览
09. Object Registry 动态表单
10. Graph Explorer 邻域查询
11. Synthetic dataset 与 Smoke E2E
12. Quality Lab / Schema diff / 备份恢复
```

**反顺序**（不建议）：先做酷炫的 3D 地球和大量图谱节点 → 最后才思考哪些节点是类、哪些是实例、如何持久化和审核。

---

# 18. 端到端示范场景与测试清单

## 18.1 场景 1：新增一种水质传感器（核心能力证明）

**前置**：Schema `Sensor` 和 `Pond` 已发布；系统处于 synthetic 数据模式。

**测试动作**：

1. UI 在 Sensor 下创建 `AmmoniaNitrogenSensor` 类，新增/继承属性。
2. 发布草稿，记录版本和 diff。
3. `GET /ontology/classes/{iri}` 能返回父类、字段、SHACL 摘要。
4. Object Registry 自动出现新类，依据元数据生成字段表单。
5. 新建一个实例并连接 `POND-001`。
6. `SPARQL` 查询 `Sensor` 的所有子类实例，应包括新的氨氮传感器（按规定的推理设置）。
7. 图谱和地图检索能找到该实例及关系（若有已绑定的可用坐标）。
8. 重启数据库与服务后重复检查，结果保持一致。

**判定成功**：不修改 Object Registry 页面源码，就能展示/录入一种新传感器；新增了业务特有字段但继承了通用字段，并通过后端约束检查。

## 18.2 场景 2：非法关系不能污染知识图谱

- 用户试图使 `Sensor` 监测 `Shipment`，服务端返回 `422 LINK_ENDPOINT_INCOMPATIBLE`。
- 实例详情/实体关系列表和 RDF 图都没有这条错误关系。
- 业务日志有拒绝原因、correlation ID、actor ID，不包含未经授权的敏感数据。

## 18.3 场景 3：候选情报关系人工采纳

- 把一篇合成政策 PDF 作为 SourceDocument；模型/手动生成“适用于某水塘”的候选关系。
- Graph Explorer 默认已采纳关系查询不显示候选边；审核模式能看到虚线边和来源证据。
- Reviewer 采纳后，关系进入 accepted 图；撤回后不能继续用于生产决策。
- 数据导出必须保留原始来源、提议者/模型版本、采纳者、时间和撤回记录。

## 18.4 场景 4：地图与实体一致性

- 水塘 GeoJSON 要素 `properties.entity_id` 与 Object Registry 完全一致。
- 水塘改名后对象详情与地图信息同步；几何变化不引起 ID 变化。
- 当地图层缓存尚未更新时，显示当前投影更新时间/待同步状态，而不是旧数据冒充最新。
- 坐标输入缺少 CRS 或明显不在梁子湖区时标记核查，不静默纠正。

## 18.5 场景 5：Action 权限与幂等

- Viewer 调用 `CreateInspectionTask` 返回拒绝；Operator 可以申请并完成业务规则校验。
- 相同幂等键重复调用只产生一个执行 ID。
- 用户尝试对离线闸门执行模拟 `SetGateOpening` 时，规则拒绝并审计；真实设备适配器始终未被调用。
- 对来源拥有的字段直接 `PATCH` 返回 `403 SOURCE_OWNED_PROPERTY`。

## 18.6 自动化测试分层

| 层 | 工具/方式 | 测试重点 |
|---|---|---|
| 单元测试 | pytest / Vitest | IRI、字段类型、规则 DSL、图布局、表单 Schema |
| OWL/RDF/SHACL | RDFLib + pySHACL + 标准 SPARQL | 类继承、约束、三元组读写、错误案例 |
| 数据库集成 | 测试 PostgreSQL + 持久 RDF Store | schema migration、事务、崩溃恢复、幂等、outbox |
| API 契约 | FastAPI OpenAPI / schema snapshot | 错误码、分页、版本、权限 |
| E2E UI | Playwright | 新类→实例→关系→地图/图谱→重启后保持 |
| 安全测试 | 角色矩阵 + 负例 | 跨区域越权、SPARQL 注入、SSRF、上游敏感数据拦截 |
| 性能测试 | k6/Locust（后续） | 邻域查询、地图图层、批量导入、Action 延迟 |
| 恢复测试 | 真实备份恢复到隔离环境 | PG/RDF/原件一致性与操作可恢复 |

## 18.7 建议性能指标（初始规划，不是实际测量）

初期演示数据量：100～1000 实例、1000～10000 条关系、万级以下观测摘要；用户数量以单团队使用为假设。建议目标：对象详情 P95 < 500ms、1-hop 邻域查询 P95 < 1s、地图常规视窗查询 P95 < 1s、常用 100 节点画布拖拽接近 45–60 FPS。指标必须在固定机器、固定数据集和测试脚本下测量；不达标时先优化查询、索引、分页和画布渲染，再考虑分布式图数据库。

---

# 19. 风险、技术债和反模式

| 风险 | 为什么重要 | 防范手段 |
|---|---|---|
| 直接把 Semantica 当整个后端 | 部分 Explorer 草稿/审核/版本状态未做可靠持久化 | 包装为能力适配器，正式持久化自己实现 |
| 把每个来源表映射为核心本体类 | 业务概念被来源数据库结构绑死 | 领域模型优先，来源 Profile 做语义映射 |
| Graph DB 与 PostgreSQL 双主写 | 会出现对象身份/状态冲突 | 定义权威表和图谱投影，outbox 与水位 |
| 用 OWL domain/range 直接当拒绝规则 | OWL 偏推理，不等于封闭世界数据库约束 | SHACL + Pydantic + 权限 + 领域校验 |
| 一开始全量启用复杂 OWL 推理 | 计算/维护难，语义误推断难追踪 | 限定 RDFS 闭包，独立评估高级推理 |
| RDF 塞入高频时序/视频 | 查询成本、存储成本和维护压力暴涨 | 外部时序/对象存储权威，RDF 存链接/摘要 |
| 视觉“像 Semantica/WWV”过头 | 独立产品定位和许可风险 | 独立 design tokens、中文农业语义、原创布局 |
| 过早大量微服务化 | 本地调试和运维难，跨库状态更复杂 | 模块化单体 + adapter + 事件边界 |
| 真机控制没有设备回执闭环 | 危险的实际物理后果 | V0/V1 模拟；后期双重授权、PLC 联锁、未知状态恢复 |
| 数据来源混合而不标记 | 合成数据/AI 推断冒充真实农业事实 | data_nature + candidate/accepted + provenance |
| 本体 IRI 随意改名 | 所有业务系统引用失效 | 永久 IRI、Deprecated、迁移工具 |
| Schema 发布没有兼容检查 | 旧实例瞬间不合法 | 影响报告、分步迁移、灰度发布 |
| 地图坐标系混乱 | GIS 错位，错误影响设备归属判断 | CRS 和来源归档，几何核验 |
| 只做 UI mock 就宣称可用 | 离开演示数据后完全不可操作 | 真实 PG/RDF 持久化 + E2E + 重启验收 |
| 配置变更无法追溯 | 长期迭代不可复现 | Git、锁版本、ADR、schema release manifest |

---

# 20. 工程团队 / Coding Agent 执行指令

> 这节可以直接复制作为开发任务的**项目常驻指令**，但执行者必须自行检查当前仓库实际状态并逐项复核，不能把本方案的设计示例说成已经实现。

## 20.1 项目级指令

```text
项目：梁子湖农业 Operational Ontology Platform (LZO)

目标：搭建真实可运行、长期维护的农业本体平台，首期管理梁子湖区域实例。
核心：OWL + RDF + SHACL + SPARQL；使用 Semantica 的能力并通过 Adapter 解耦。
业务：Object Types / Properties / Link Types / Instances / Actions / Functions。
存储：PostgreSQL 管权威身份/业务事务/审计；持久 RDF Store 管语义图；PostGIS 管几何；时序表管测量。
前端：React+TypeScript 全新设计，农业湖域主题。严禁复制 liangzihu-ontology-traceability 的前端。
视觉参考：Semantica 的 Ontology Editor、Graph Explorer；WorldWideView 的地图、图层、时间轴体验，但不得照搬代码、页面或外观。

实施顺序：
1. 首先建立可运行持久化、真实 OWL/SHACL、Schema CRUD 与实体 API。
2. 使用最小农业实例证明子类继承、SHACL 校验、SPARQL 查询和服务重启持久化。
3. 再做 Ontology Studio、Object Registry、Graph Explorer、Geo Workspace 的实际 API 联动。
4. Action Engine 先业务任务与模拟设备，不接现场设备。
5. 情报、溯源、IoT 真数据在证据、权限和版本一致性通过后逐步加入。

工程纪律：
- 每次提交代码前更新 OpenAPI / schema manifest / 测试。
- 发现设计冲突先创建 ADR 并解释取舍，不偷偷变更核心架构。
- 不做假的接口成功提示、前端写死图谱关系、演示数据冒充现场。
- 所有前端操作必须接入真实后端 API，异常必须可见。
- 修改已发布本体不得直接覆盖 TTL；通过新版本和影响校验。
- 新增源数据只走 preview/apply，不允许绕过证据和审核。
- 无论任何情况，真实设备控制默认关闭；高风险请求必须人工授权。
- 每个迭代交付运行命令、变更清单、截图、已通过测试和未完成项。
```

## 20.2 第一张开发任务卡（应当最先执行）

**标题**：`LZO-P0-01: Setup durable ontology platform skeleton`

**目标**：在干净开发环境可一键启动 API、PG、RDF Store、React；可以读取一份 OWL Schema 和一份演示实例 RDF。

**实现步骤**：

1. 新建 monorepo 和 Python/TS 锁文件；前端不引旧仓库。
2. 配置 docker compose 的 PostgreSQL/PostGIS 数据卷；Oxigraph 单独持久数据目录。
3. 实现 `GraphRepository` 统一接口与 `OxigraphGraphRepository` 适配器。
4. 准备 `lzo-core.ttl` 和 `shapes.ttl`，包含 `Pond`、`Sensor`、`WaterQualitySensor`、`monitors`。
5. 实现 `POST /validate/instance` 和 `GET /health/ready`。
6. 创建 React 自有主题、页面布局、`/ontology/studio` 页面；显示 Schema 的实际 class tree。
7. 增加 pytest/Playwright 基础测试，重启后检查数据仍存在。

**Definition of Done**：不使用 mock 直接读取真实 RDF 数据；有持久化文件/目录，有 SHACL 失败例子，前后端运行指令可由另一台机器复现；README 列明当前尚未实现的部分。

## 20.3 第二张开发任务卡

**标题**：`LZO-P0-02: Ontology Draft / Publish / Versioning`

实现 Schema draft 到 published 的端到端状态机；保留每次 OWL/SHACL 文件快照、SHA-256 和审计；发布前运行兼容性校验；失败状态可重试；可从 UI 查看继承属性预览。

**DoD**：发布之后重启系统，版本仍存在；同一个包的两版 schema 能正确查询和 diff；不合法/冲突版本不能覆盖正在使用的 schema。

## 20.4 第三张开发任务卡

**标题**：`LZO-P0-03: Dynamic Object & Link Registry`

Object API 以正式发布的类和 Shape 为依据自动校验，前端按表单 schema 渲染字段，Link API 校验端点和关系状态，所有写入带审计与 Outbox。

**DoD**：在前端新建一个传感器子类（无需更改对象列表/表单源码），发布后创建其实例，连接已有水塘；查询、编辑、停用、重启持久化均成功。

## 20.5 每轮 Agent 输出格式

```markdown
# 本轮实现报告
- 本轮目标：
- 修改目录与文件：
- 新增/修改 API 契约：
- 新增/修改 OWL/SHACL/迁移：
- 本地运行方法：
- 自动测试及通过情况（附原始命令和结果）：
- 前端实际截图/录屏：
- 数据是 synthetic/test/verified 哪种：
- 已知限制与未做事项：
- 下一轮建议及架构风险：
```

输出禁止“全部完成”式笼统汇报。测试未执行时必须标记 `not run`，不得把 `skipped` 当 `passed`。

---

# 21. 长期演进路线、决策记录与参考资料

## 21.1 长期版本路线

| 版本 | 应有核心能力 | 不追求什么 |
|---|---|---|
| V0.1 | 可运行 RDF/OWL/SHACL+基础 API | 不追求漂亮 3D 地图 |
| V0.2 | Schema Studio、版本发布、实例管理、RDFS 子类推断 | 不追求全国农业本体覆盖 |
| V0.3 | Graph Explorer、证据、候选事实审核 | 不追求 AI 全自动决策 |
| V0.4 | GIS 双向联动、第一批授权真实来源 | 不追求直接控制现场设备 |
| V0.5 | Action Engine、模拟执行、工作流/审批 | 不追求完整设备协议栈 |
| V1.0 | 真实农业场景可长期运维、权限/恢复/监控通过验收 | 不追求完整复制 Palantir 全部产品 |
| V2.x | 情报智能关联、深度溯源、多园区/跨区域扩展 | 按实际需求驱动扩展 |

版本号是规划标识，实际落地应以测试和产品验收为标准，而不是达到日期便自动升级。

## 21.2 推荐立即建立的 ADR

| 编号 | 决策标题 | 必须明确的内容 |
|---|---|---|
| ADR-001 | RDF Store 选择：Oxigraph vs Jena Fuseki | 部署/持久化/SPARQL/备份/SHACL 适配测试结果 |
| ADR-002 | 唯一权威数据与 PG↔RDF 投影 | 权威字段、事件顺序、失败恢复、投影水位 |
| ADR-003 | URI/IRI、UUID 和 Source Binding | 永久命名空间、外部主键映射、去重原则 |
| ADR-004 | OWL、SHACL 与业务规则职责划分 | 哪些是推理，哪些是验证，哪些是操作策略 |
| ADR-005 | Schema 发布、兼容和弃用 | Draft/Review/Publish/Migrate/Rollback |
| ADR-006 | 前端框架、设计系统与视觉版权 | 不复制旧前端/参考站素材，图谱组件选型 |
| ADR-007 | Action Engine 与设备安全边界 | 数据权属、幂等、审批、设备回执与未知状态 |
| ADR-008 | Geo 数据标准与来源 | 边界授权、EPSG、几何主库、GeoSPARQL 使用范围 |

## 21.3 公开参考资源与核查说明

### 产品与交互

- Palantir Ontology 官方总览：<https://www.palantir.com/docs/foundry/ontology/overview/>
- Palantir 类型参考（Object / Link / Action / Interface）：<https://www.palantir.com/docs/foundry/object-link-types/type-reference>
- Palantir Link Types：<https://www.palantir.com/docs/foundry/object-link-types/link-types-overview>
- Palantir Action Rules：<https://www.palantir.com/docs/foundry/action-types/rules>
- Semantica 主仓库：<https://github.com/semantica-agi/semantica>
- Semantica Ontology 文档：<https://github.com/semantica-agi/semantica/blob/main/docs/guides/ontology.md>
- Semantica SHACL 文档：<https://github.com/semantica-agi/semantica/blob/main/docs/guides/shacl-validation.md>
- Semantica 存储后端特性矩阵：<https://github.com/semantica-agi/semantica/blob/main/docs/storage-backends.md>
- Semantica React Explorer：<https://github.com/semantica-agi/semantica/tree/main/explorer>
- Semantica Ontology Editor 源码：<https://github.com/semantica-agi/semantica/blob/main/explorer/src/workspaces/OntologyWorkspace/OntologyEditor.tsx>
- Semantica 后端 Ontology API 源码（审核草稿与部分版本状态使用 app.state，采用前需重构持久化）：<https://github.com/semantica-agi/semantica/blob/main/semantica/explorer/routes/ontology.py>
- WorldWideView 地理界面演示：<https://demo.worldwideview.dev/>
- Operational Ontology 参考（Action、Source Ownership）：<https://github.com/gura105/operational-ontology>
- 旧梁子湖本体仓库（仅用作对象术语/字段参考）：<https://github.com/lianggeming14/liangzihu-ontology-traceability>

### 标准

- W3C RDF 1.1 Concepts：<https://www.w3.org/TR/rdf11-concepts/>
- W3C OWL 2 Overview：<https://www.w3.org/TR/owl2-overview/>
- W3C SHACL Recommendation：<https://www.w3.org/TR/shacl/>
- W3C SPARQL 1.1 Query：<https://www.w3.org/TR/sparql11-query/>
- W3C SKOS Reference：<https://www.w3.org/TR/skos-reference/>
- W3C SOSA/SSN：<https://www.w3.org/TR/vocab-ssn/>
- W3C PROV-O：<https://www.w3.org/TR/prov-o/>
- OGC GeoSPARQL 1.1：<https://www.ogc.org/standards/geosparql/>

### 工程选型

- Oxigraph：<https://github.com/oxigraph/oxigraph>
- Apache Jena/Fuseki：<https://jena.apache.org/documentation/fuseki2/>
- RDFLib：<https://github.com/RDFLib/rdflib>
- pySHACL：<https://github.com/RDFLib/pySHACL>
- React Flow：<https://reactflow.dev/>
- Sigma.js：<https://www.sigmajs.org/>
- MapLibre GL JS：<https://maplibre.org/maplibre-gl-js/docs/>
- PostGIS：<https://postgis.net/>

### 参考项目核查结论（2026-10-20）

- Semantica 仓库确有 ontology、reasoning、triplet_store、Explorer React 的实际实现，但**各存储适配器和功能的成熟度需单独验收**；其文档明确不应将 OWL 文件的生成与全部 OWL 语义推理混为一谈。
- Semantica Explorer 的草稿/提案/版本 API 中部分以 `request.app.state` 保存内存状态，本方案**不直接复用这部分后端持久化逻辑**。
- WorldWideView 页面可核对的交互包括地图/地理视图、区域检索、图层、过滤、时间轴及坐标状态等；本方案以此作为体验方向参考，**没有声称获得其源代码或素材复用授权**。
- 原 `liangzihu-ontology-traceability` 仓库虽有关系数据库实现与农业对象定义，但本方案不以它的前端、数据结构或后端为构建前提。
- 真实梁子湖行政区界、设备位置、农田/水塘空间数据、农业生产记录均须后续授权获取；本方案的示例均为结构设计和合成演示数据，**不是现场事实**。

## 21.4 最终建设原则总结

**先打通技术闭环，再扩充农业内容；先建立长期稳定的语义和身份，再追求复杂前端；先进行可控业务操作，再连接真实设备。**

这个框架的真正长期价值不在于一开始有几千个本体类，也不在于 3D 地图多漂亮，而在于：**每次增加新对象、新属性、新关系、新数据源和新业务动作，都能在保持稳定 ID、标准语义、权限、安全、审计和版本管理的情况下持续扩展**。

完成 V0/P0 后，团队应拿着“新增一种水质传感器—创建实例—关联水塘—SHACL 校验—SPARQL 查询—地图展示”的实测链路作为基础，再迭代走向完整的梁子湖农业 Operational Ontology Platform。

---

*文档结束 · V1.0 · 所有示例代码需通过实际环境校验、版本锁定和安全审计后投入开发/生产。*
