# 离线交付与完整验收 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在真实组件上证明三个农业场景完整可用，并交付离线包、可恢复备份及可复现性能报告。

**Architecture:** 固定种子数据通过正式接入和治理 API 建立，浏览器和性能测试连接同一部署；隔离网络验证外网断开，写入屏障与一致性水位协调多个存储的恢复。

**Tech Stack:** Docker Compose、pytest、Playwright、Locust、PostgreSQL 工具、Jena 标准导出/备份、Python 报告生成器。

**Spec:** [已确认设计](../specs/2026-10-20-lzo-platform-p0-p1-design.md) §14–17、T01–T20；遵守[总计划](2026-10-20-lzo-p0-p1-implementation.md)及其全部接口和约束。

## Global Constraints

- 标准验收环境为 8 核 CPU、32GB 内存、SSD，10 并发、1 万实例、10 万条基础关系。
- 形成可复现的自动化测试报告，记录 P50/P95、吞吐量、CPU、内存、错误率；基准不是容量上限。
- 核心功能断开外网仍正常运行，所有关键静态资源和依赖能够内网部署。
- 同时支持在线底图、MBTiles、PMTiles；业务空间数据由平台自身管理，与底图解耦。
- 不以已缓存页面冒充离线可用，不以成功受理冒充异步任务完成，不以未执行或跳过冒充通过。

## Review Focus

D02 负责不含浏览器缓存的离线登录、刷新与地图；D03 负责历史、证明、权限及原始快照恢复；D04 将预期冲突/拒绝与非预期错误分开，覆盖长任务水位和资源峰值。其余三项沿用总计划并在 D01 端到端重放。

## 文件分工

`scripts/seed_dataset.py` 只生成和调用正式导入 API；`scripts/backup.py/restore.py` 管一致性与恢复，不能拥有修改历史的业务入口；`tests/performance/` 管负载和指标，`docs/verification/` 保存有环境证据的结果与未覆盖项。

### Task D01：三场景固定种子数据与完整功能验收

**Files**
- Create: `scripts/seed_dataset.py`、`tests/fixtures/scenes.yaml`、`tests/fixtures/source_profiles.yaml`
- Create: `tests/e2e/farm-field.spec.ts`、`tests/e2e/pond.spec.ts`、`tests/e2e/orchard.spec.ts`、`tests/e2e/shared-device.spec.ts`
- Create: `apps/api/tests/integration/test_complete_scenarios.py`、`apps/api/tests/integration/test_schema_migrations.py`
- Create: `docs/verification/scenario-catalog.md`

**Interfaces**
- Consumes: S06 基础本体包；S02发布/S03写入/S04推理/S05查询；I01–I06 接入与重算；U01–U06 浏览器流程。
- Produces: `generate_dataset(config: DatasetConfig) -> DatasetManifest`，`DatasetConfig(seed: int, instances: int, base_relations: int, source_profiles: list[str])`；`DatasetManifest(seed, counts: dict[str,int], file_digests: dict[str,str], namespace: str, generated_at: str)`。
- CLI: `python scripts/seed_dataset.py --seed 20261020 --instances 10000 --relations 100000 --output build/seed --apply`；`--apply` 只能指向明确的测试实例，要求其配置 `environment_kind=test`，不能覆盖未知用户数据。

- [ ] **Step 1：写每个场景的完整业务断言。**
  ```python
  @pytest.mark.parametrize("scene", ["farmland", "pond", "orchard"])
  def test_full_semantic_governance_cycle(scene, scenario_client):
      result = scenario_client.run_full_cycle(scene)
      assert result.identity_preserved_across_sources
      assert result.invalid_record_rejected_by_shacl
      assert result.subclass_and_inverse_inferred
      assert result.inference_retracted_after_last_support_removed
      assert result.sparql_and_map_reference_same_iri
      assert result.unrelated_region_cannot_read
      assert result.evidence_and_rule_version_traceable
  ```
  本文件定义 `scenario_client`，通过真实 API 创建两名审核角色、不同区域、独立 PG/MySQL/文件来源；每个属性是对应查询结果断言，不能用固定 True 填充。加入共享农机单一身份、不同区域操作权、长期裁决未变化仍有效、新冲突复核、历史前后状态及责任人断言。迁移测试验证唯一 Alembic head，空库升级与上个发布版本升级皆成功。
- [ ] **Step 2：执行 `pytest tests/integration/test_complete_scenarios.py tests/integration/test_schema_migrations.py -q`，以及三个场景 Playwright 文件；先确认缺失流程导致具体失败。**
- [ ] **Step 3：实现生成器、场景与跨模块测试装配。**
  固定种子、合成命名空间和稳定生成规则，保证重放身份一致且标注 synthetic；生成 10,000 个领域实例和 100,000 条基础对象关系的验收配置，同时提供可调整的小集。类型三元组、属性断言、派生三元组、历史和来源支持另计，不混入 100,000。按 Schema 合法端点生成关系，不用任意连边填数量；三个场景都覆盖传感器及其他代表性设备，整个数据集包含七类设备。
  通过正式导入/审核 API 生成候选、采纳、同步状态；初始化管理员与测试账户仅走隔离环境专用脚本。浏览器场景走实际表单、审批与查询，不直接写业务表绕过核心流程。
- [ ] **Step 4：运行单元/集成相关测试、三个场景 E2E 和共享设备 E2E；核对实际统计与 manifest，重启后重跑关键读取。**
- [ ] **Step 5：提交 `test: verify full farmland pond and orchard workflows`，记录场景目录和实际未通过项；所有场景完成才勾选 T01。**

### Task D02：离线包、冷启动与无外网依赖

**Files**
- Create: `scripts/package_offline.py`、`scripts/verify_bundle.py`、`infra/compose/compose.offline-test.yaml`
- Create: `tests/e2e/offline-cold-start.spec.ts`、`tests/e2e/offline-map-resources.spec.ts`
- Create: `docs/operations/offline-deployment.md`、`docs/verification/offline-report.md`
- Modify: `infra/versions.lock.json`、`infra/compose/compose.yaml`

**Interfaces**
- Consumes: 锁定镜像、F03身份、U05本地地图、D01数据；F01同源代理。
- Produces: `build_bundle(output_dir: Path, version_manifest: Path) -> Path`；`verify_bundle(bundle_dir: Path) -> BundleVerification`，其中 `BundleVerification(valid: bool, missing: list[str], mismatches: list[str])`。
- Bundle: `manifest.json`、镜像归档、Compose/配置模板、版本化 OWL/SHACL/规则、迁移、必要本地资源与演示地图、启动/校验文档；密钥由目标环境生成或独立安全交付，不在公共演示包中复用真实凭据。

- [ ] **Step 1：写无缓存冷启动和资源完整性测试。**
  ```typescript
  test('离线新会话仍能登录并使用三种底图配置', async ({ browser }) => {
    const context = await browser.newContext({ storageState: undefined });
    const page = await context.newPage();
    await loginThroughLocalKeycloak(page);
    await openOfflineMapAndPan(page, 'pmtiles');
    await expect(page.getByTestId('selected-object-iri')).toContainText('synthetic');
    await expireSessionAndRefreshThroughLocalIdp(page);
    await expect(page.getByRole('button', { name: '退出登录' })).toBeVisible();
  });
  ```
  helper 在本测试文件/`tests/e2e/helpers/offline.ts` 定义，均调用真实本地 UI/测试 realm；同时测 MBTiles 栅格/MVT 配置、PMTiles Range、glyph/sprite 缺失报错、超出覆盖范围提示、外部数据源离线后已有对象/关系仍可查。
- [ ] **Step 2：运行离线测试，确认未打包字体/瓦片或未配置本地身份时产生明确失败。**
- [ ] **Step 3：打包固定版本并设置隔离网络。**
  `compose.offline-test.yaml` 使用内部容器网络，浏览器测试容器仅可达本地代理/身份/平台/地图，外网出口不可达；验证阻断探针失败且内网探针成功。禁止用 Playwright 全局 `offline=true` 断掉本地服务，也不改用户主机全局网络设置。停止全部测试服务，清浏览器数据后从包 `docker load` 并启动，启动不拉取外部镜像、包、字体或脚本。
  模拟在线底图不可达并切换本地资源，原对象 ID/几何/关系不变；核心启动不能依赖在线服务健康。记录资源许可证、摘要及自带本地演示瓦片实际范围。
- [ ] **Step 4：执行 `python scripts/verify_bundle.py build/offline` 和隔离网络的 `npx playwright test tests/e2e/offline-cold-start.spec.ts tests/e2e/offline-map-resources.spec.ts`；输出完整离线报告和外部请求失败清单，确保失败不影响核心结果。**
- [ ] **Step 5：提交 `build: deliver verified offline runtime bundle`；大镜像和地图归档在构建产物目录，不提交 Git。**

### Task D03：一致备份、完整历史恢复与升级迁移

**Files**
- Create: `scripts/backup.py`、`scripts/restore.py`、`scripts/verify_history.py`
- Create: `apps/api/src/lzo/operations/{maintenance,backup_manifest}.py`
- Create: `apps/api/tests/integration/test_backup_restore.py`、`apps/api/tests/integration/test_history_integrity.py`
- Create: `docs/operations/backup-restore.md`、`docs/operations/upgrade.md`、`docs/verification/restore-report.md`

**Interfaces**
- Consumes: F02 ledger/outbox、F04文件、F05任务水位、S01图谱、S04证明、U05地图；部署管理员独立运维身份。
- Produces: `enter_write_barrier(actor: ActorContext) -> BarrierRef`、`leave_write_barrier(actor, barrier_id: UUID) -> None`；`BarrierRef(id, ledger_watermark: int, rdf_watermark: int, inference_watermark: int)`。
- Produces: `create_backup(destination: Path, barrier: BarrierRef) -> BackupManifest`；`restore_backup(manifest: Path, target_environment: str) -> RestoreReport`。manifest 含全部文件摘要、存储版本/水位、授权/身份信息和加密备份标识；不在报告中输出密钥。
- CLI: `python scripts/backup.py --output build/backups/run-001`；`python scripts/restore.py --manifest ... --target restore-test`；拒绝未明确标为隔离恢复目标的环境。

- [ ] **Step 1：写历史与水位一致性测试。**
  ```python
  def test_restore_retains_history_proofs_and_authority(restore_scenario):
      before = restore_scenario.capture_versioned_state()
      report = restore_scenario.backup_and_restore_to_isolated_stack()
      assert report.current_state_digest == before.current_state_digest
      assert report.history_chain_digest == before.history_chain_digest
      assert report.old_proof_is_explainable
      assert report.actor_ids_unchanged
      assert report.revoked_user_read_status == 404
  ```
  fixture 先制造对象两次修改、几何变化、审批/裁决、推理生成和撤回、失败任务与共享撤权；加入备份文件缺失/摘要错误拒绝、使用隔离管理员篡改历史后的校验失败负例。该篡改只在一次性测试数据库进行。
- [ ] **Step 2：执行两份测试，确认未实现屏障/历史备份时具体失败。**
- [ ] **Step 3：实现组合备份与恢复。**
  进入写入屏障，拒绝新业务写与新任务领取，等待已有事务结束并推进已提交 outbox/推理到稳定水位。记录一致快照；备份 PG/PostGIS、Keycloak 数据库及配置、RDF 标准导出和受支持的 TDB2 备份、历史证明、发布产物、证据/原始快照、几何、地图、版本和校验链。不得复制正在写入的任意 TDB2 文件冒充一致备份。
  超时或组件错误则中止并解除屏障，报告未完成；恢复先校验摘要/版本，在新目标恢复身份和权威记录，再验证或重建当前 RDF/推理投影，不覆盖旧修订。复核会话/权限和所有未完成作业，不自动执行失效授权任务。保留加密备份和离线恢复密钥交付说明。
- [ ] **Step 4：从空目标执行恢复、历史对比和任务续跑检查；执行支持的版本升级与失败恢复演练，报告恢复耗时、实际恢复点和不支持的降级路径。**
- [ ] **Step 5：提交 `feat: verify consistent backup and historical recovery`。**

### Task D04：固定基准、混合负载和自动性能报告

**Files**
- Create: `tests/performance/locustfile.py`、`tests/performance/profiles/baseline.yaml`、`tests/performance/profiles/soak.yaml`
- Create: `tests/performance/collect_metrics.py`、`tests/performance/report.py`、`tests/performance/test_report_math.py`
- Create: `scripts/run_performance.py`、`docs/verification/performance-method.md`
- Output: `build/reports/performance/<run-id>/{environment.json,raw.csv,resources.jsonl,report.html,report.json}`

**Interfaces**
- Consumes: D01数据 manifest、所有业务 API、部署指标与作业完成水位。
- Produces: `run_profile(profile: Path, output: Path) -> RunResult`；`summarize_run(raw_dir: Path) -> PerformanceReport`。
- DTO: 每个样本含 `operation, started_at, duration_ms, outcome, expected_error, job_id, phase`；phase 为 `accepted/end_to_end/read`；报告按操作给出样本数、P50/P95、吞吐、错误分类、CPU/内存时序/峰值、队列和投影滞后。

- [ ] **Step 1：写统计及正确性测试。**
  ```python
  def test_report_does_not_mix_expected_rejection_with_failure():
      report = summarize_samples([
          sample("read", 10, "ok"), sample("read", 30, "ok"),
          sample("edit", 20, "conflict", expected=True),
          sample("sync", 50, "server_error"),
      ])
      assert report.unexpected_errors == 1
      assert report.expected_rejections == 1
      assert report.by_operation["read"].p50_ms == 20
  ```
  `summarize_samples(samples: list[Sample]) -> PerformanceReport`、`Sample` 定义于 report.py；明确百分位线性插值算法、缺样本显示 N/A 而非0。`sample` 是本测试文件构造器。另验证异步受理/完成分别统计、无运行记录不能报告通过。
- [ ] **Step 2：执行 `pytest tests/performance/test_report_math.py -q`，确认失败；从根测试容器运行，不误用 api 工作目录。**
- [ ] **Step 3：实现可配置负载与指标收集。**
  baseline：seed=20261020，10 用户、10,000实例、100,000基础关系，60秒预热、300秒测量；soak：同基准、至少3600秒混合负载。时长是工程默认可修改，不是业务限制。工作负载覆盖 RDF写入/对象/SPARQL/空间/关系查询、SHACL正反例、推理与撤回、PG/MySQL同步、裁决、规则重算、并发编辑；离线与恢复耗时分别由D02/D03运行关联报告。
  固定10个独立会话，不共享一个超级管理员令牌；账号包含不同权限和审核职责。读写比例写入配置，数据生成、归零方法和水位完成判定可重复。采集各容器及宿主CPU/内存，记录硬件、SSD/文件系统、虚拟化和容器限制；若不是标准8核32GB SSD，明确标为探索性结果并保留标准环境验收未执行状态。
- [ ] **Step 4：执行 `python scripts/run_performance.py --profile tests/performance/profiles/baseline.yaml`，再执行soak配置；生成HTML/JSON及原始指标。核对业务正确性、不丢失、不越权、资源有界、无未解释错误；不因延迟不理想删掉样本或跳过负载。**
- [ ] **Step 5：提交 `test: add reproducible load and stability reporting`；报告区分实际结果、失败及未覆盖项，不新增未经确认的P95硬门槛。**

### Task D05：交付总验收、文档和证据索引

**Files**
- Create: `docs/verification/requirements-matrix.md`、`docs/verification/release-checklist.md`、`docs/operations/deployment.md`、`docs/operations/troubleshooting.md`
- Create: `scripts/verify_release.py`、`.github/workflows/verify.yml`
- Modify: `README.md`、`infra/versions.lock.json`

**Interfaces**
- Consumes: F/S/I/U/D 全部任务结果与测试产物；不新增产品功能。
- Produces: `verify_release(evidence_root: Path) -> ReleaseVerdict`，`ReleaseVerdict(passed: bool, failed: list[str], not_run: list[str], artifact_digests: dict[str,str])`。
- Traceability: 每个R/T行写任务、自动测试路径、运行环境、报告路径、状态、缺陷/限制；不能仅打“已支持”勾。

- [ ] **Step 1：定义交付门与缺失证据负例。**
  `tests/release/test_evidence_manifest.py` 检查：
  ```python
  def test_missing_offline_and_restore_evidence_fails(evidence_bundle):
      evidence_bundle.omit("T14")
      evidence_bundle.omit("T16")
      verdict = verify_release(evidence_bundle.path)
      assert not verdict.passed
      assert {"T14", "T16"} <= set(verdict.not_run)
  ```
  本文件定义临时元数据fixture，仅验证证据索引逻辑；真实报告必须包含退出码、时间、版本与摘要。
- [ ] **Step 2：执行 `pytest tests/release/test_evidence_manifest.py -q`，确认无法将缺失检查报告为通过。**
- [ ] **Step 3：汇总文档与统一验证命令。**
  README覆盖本地启动、标准环境、真实组件与合成数据标记、前端使用路径、运维入口和当前限制。部署/升级/备份/恢复/连接器/新类型/规则治理说明使用已验证命令；CI调用同锁版本容器执行测试、OpenAPI生成差异和前端构建，支持本地离线同等执行。生成SBOM/许可证及发布清单，配置模板不含真实凭据。
- [ ] **Step 4：运行本任务检查和一次完整验收入口，完成整分支审阅；核对 R01–R36、T01–T20 全覆盖且所有未执行项显式列出。只在实际全通过后声明 P0＋P1 完整交付，内网部署不触发公网发布。**
- [ ] **Step 5：提交 `docs: publish verified platform delivery evidence`，按用户授权的本地/内网方式交付；执行分支整合前遵循相应工作流。**
