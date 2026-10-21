# LZO 前端工作台与地图 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过真实 API 交付中文农业本体工作台，让农田、水塘、果园使用同一动态建模、治理、地图与图谱交互。

**Architecture:** React 页面只消费 OpenAPI 生成契约；BFF 在服务端保存 OIDC Token，浏览器使用同源受保护会话。地图资源服务独立于业务几何，通过本地 MBTiles/PMTiles、样式、glyph 和 sprite 提供断外网可用地图；所有业务数据仍经过平台授权 API。

**Tech Stack:** React、TypeScript、Vite、TanStack Query、XYFlow、Cytoscape.js、MapLibre GL JS、PMTiles、FastAPI、Keycloak、Vitest、Testing Library、Playwright。

**Spec:** [已确认设计](../specs/2026-10-20-lzo-platform-p0-p1-design.md)；[总计划与公共契约](2026-10-20-lzo-p0-p1-implementation.md)。本计划只描述实施步骤，当前不创建产品代码或安装依赖。

## Global Constraints

- 范围：完整 P0＋P1，包含本轮讨论确认的多源治理、规则重算、常用推理和离线部署能力。
- 农田、水塘、果园并行建模和验证；暂不纳入畜牧场景。
- 设备覆盖传感器、摄像头、闸门、水泵、灌溉设备、农机和无人机，支持前端动态扩展。
- 所有有效按钮调用真实 API；不把内存数据、浏览器缓存或 mock 成功作为完成依据。
- 独立中文湖域农业视觉，不复制旧梁子湖、Semantica 或 WorldWideView 的代码、布局、图标及素材。
- 高风险操作含新增 Schema 结构，提交人与审核人不同；服务端判级，前端不能降级。
- 原始、候选、采纳、派生分别展示；采纳不等于 verified；历史按当前权限读取，不能编辑旧修订。
- 核心功能断开外网仍正常运行，所有关键静态资源和依赖能够内网部署。
- 稳定设备 ID、主管理归属、服务关系、共享权限独立；不因地图切换或关联区域复制设备。
- 不建设 P2 Action Engine、真实设备控制、3D、视频平台、全平台任意历史时点回放。
- 标准环境 8 核 CPU、32GB 内存、SSD；10 并发、1 万实例、10 万基础关系是报告基准，不是 UI 容量上限。

## Review Focus

1. 多标签页和会话续期失败：清理用户缓存且只重试一次，不能重复提交命令；由 U01 测试覆盖。
2. 草稿未保存、断网和并发冲突同时出现：保留本地未提交操作且不覆盖服务端新版本；由 U02 测试覆盖。
3. 数据源配置改变或裁决过期：不能凭旧预览执行导入、发布或重算；由 U04 测试覆盖。
4. 权限撤销后浏览器仍有图谱/地图缓存：移除受限内容并重查，不继续显示详情、计数和下载；由 U05/U06 测试覆盖。
5. 缺失字体、地图包层级或 PMTiles Range 响应异常：明确降级原因，不能伪装离线底图加载成功；由 U05 测试覆盖。

---

## 文件边界与执行依赖

| 文件/目录 | 唯一职责 | 实现任务 |
|---|---|---|
| `apps/web/src/app/{router,Shell,SessionBoundary}.tsx` | 路由、原创框架、会话门禁 | U01 |
| `apps/web/src/api/{generated,client}.ts` | 生成类型与同源客户端 | U01，后续任务重新生成 |
| `apps/web/src/shared/{errors,commands,selection,JobStatus}.tsx` | 错误、命令重试、对象选择、作业展示 | U01/U04/U05 |
| `apps/web/src/features/studio/` | 本体草稿、继承、Shape、发布 | U02 |
| `apps/web/src/features/objects/` | 动态表单、实例与关系、来源 | U03 |
| `apps/web/src/features/sources/`、`governance/` | 接入、冲突与规则重算 | U04 |
| `apps/web/src/features/graph/`、`maps/` | 实例图、受控查询、地图 | U05 |
| `apps/api/src/lzo/maps/` | 地图资源登记、校验、瓦片及 Range 服务 | U05 |
| `apps/web/src/features/{access,history,system}/` | 授权、修订对比、健康与设置 | U06 |
| `apps/web/public/assets/` | 本地字体、原创图标、许可证清单 | U01/U05 |
| `tests/e2e/workbench/` | 浏览器连接真实服务的验收 | 每任务 |

按 U01 → U02 → U03 → U04 → U05 → U06 推进；U05 地图资源后端可在 F04、S03 后先交付。
U01 依赖 F01–F03；U02 依赖 F05/F06、S01–S02；U03 完整验收依赖 S03、F03、I01/F04证据和I06来源责任链；U04 依赖 I01–I06；U05 依赖 S03–S05、F04；U06 依赖 F03/F05/F06 与全部 U 页。
不得用等待后端为由创建永久静态假页面；缺失 API 先完成所属后端任务的接口及测试，再写有效 UI。

## 共享 API、测试与命令约定

业务 URL 均带 `/api/v1`；F01健康探针保持 `/health/live`、`/health/ready`。下文业务接口省略前缀。`apps/web/src/api/generated.ts` 由 `contracts/openapi.json` 生成，不手写 DTO。
客户端使用 `openapi-fetch` 的 `createClient<paths>()`，`paths` 从生成文件导入；请求/响应类型直接从具体路由推导，不改名为第二份 REST 模型。
客户端函数为 `api = createApiClient(): Client<paths>`；`mutate` 统一附带 `Idempotency-Key`，既有资源附 `If-Match`，对失败重试复用同一命令键，不自动重试非幂等变更。
JSON 错误按总契约 `{code,message,request_id,details}` 呈现；401、403、404、409、422、428 各有明确 UI；202 只显示已受理，等待 `/jobs/{id}` 及投影/推理状态。异步结果按F05 `JobView.result_ref`定位具体报告/资源，不能把JobRef当报告。OAuth登录/回调遵循F03协议，不能套用一般业务幂等header拦截浏览器跳转。
`scripts/export_openapi.py` 由 U01 创建，以 `create_app().openapi()` 生成契约；U01 增加 `npm run api:generate` 与 `npm run api:check`，生成流程禁止联网取类型。
浏览器测试共用 `tests/e2e/support/workbench.ts`，U01创建`login(page, username): Promise<void>`、`logout(page): Promise<void>`和`createNamedContext(browser, username): Promise<{context: BrowserContext,page: Page}>`；U02在F05接口交付后增加`waitForJob(page, jobId): Promise<void>`，避免U01提前依赖未生成的jobs类型。
测试账号/密码从 F03 的隔离 Keycloak 测试 realm 配置读取，不提交实际密码；fixture 由各所属服务的现有测试种子命令准备，不增加绕过权限的生产 `/test` 路由。
U01 创建 `scripts/seed_workbench.py`：只在 `APP_ENV=test` 通过`--stage auth|studio|objects|sources|maps`调用对应已交付F/S/I服务，准备隔离数据并输出`.runtime/workbench-fixtures.json`。U01只实现auth阶段；后续任务逐个添加阶段，不在U01导入尚不存在的S/I模块。
fixture 清单只存测试 ID、IRI、revision、账号别名与测试资源路径，不存 Token/密码；测试启动时重新生成并禁止跨验收环境复用。
每个红/绿步骤使用同一命令；红灯必须是明确断言失败或尚缺的目标模块，不接受网络不可达、凭证错误作为功能红灯。
以下短命令分别在 F01 的 Compose `web-test`、`api-test`、`e2e-test` 工作目录内运行；执行证据使用总计划的完整 Compose 命令。
前端红绿：`npm run test -- <file>`；浏览器红绿：`npx playwright test <file>`；后端红绿：`pytest <file> -q`。
各任务完成后另运行 `npm run typecheck`、`npm run build`、`npm run api:check`；只在相关文件变化时重复已通过检查。

### U01：真实 OIDC/BFF 会话与中文工作台

**Files**
- Create: `apps/web/package.json`、`apps/web/package-lock.json`、`apps/web/tsconfig.json`、`apps/web/vite.config.ts`、`apps/web/index.html`、`apps/web/Dockerfile`、`apps/web/src/main.tsx`。
- Create: `scripts/export_openapi.py`；Modify: F01已创建的`package.json`、`package-lock.json`、`playwright.config.ts`、`infra/testing/Dockerfile.e2e`，扩展工作台测试；根npm不复制web依赖。
- Create: `apps/web/src/app/router.tsx`、`Shell.tsx`、`SessionBoundary.tsx`。
- Create: `apps/web/src/api/client.ts`、`apps/web/src/shared/errors.tsx`、`commands.tsx`。
- Create: `apps/web/src/styles/tokens.css`、`layout.css`、`apps/web/public/assets/fonts/LICENSES.md`。
- Modify: `infra/compose/compose.yaml`、`infra/proxy/nginx.conf`、`contracts/openapi.json`；补齐F01预留web-test/e2e-test镜像与同源静态页面。
- Create: `tests/e2e/support/workbench.ts`、`scripts/seed_workbench.py`。
- Test: `apps/web/src/api/client.test.ts`、`tests/e2e/workbench/session-shell.spec.ts`。

**Interfaces**
- F03 owns BFF OIDC session routes; U01 implements their browser client, not a second auth service.
- Consume: `GET /session/login`、`GET /session/callback`、`GET /session`、`POST /session/refresh`、`POST /session/logout`，以及 F03 定义的 CSRF 和权限摘要契约。
- Produce: `createApiClient(): Client<paths>`、`SessionBoundary(): React.JSX.Element`、`Shell(): React.JSX.Element`。
- Produce: `formatApiError(error: unknown): string`、`commandKey(): string`；错误包含 request_id 供排障。
- UI routes: `/overview`、`/ontology/studio`、`/objects`、`/graph`、`/geo`、`/quality`、`/sources`、`/governance`、`/access`、`/settings`。

- [ ] **1. 写真实登录、Cookie 和导航失败测试。**

U01先创建最小测试启动配置，再写下列功能断言；红灯来自功能缺失，不能只是找不到npm测试脚本。`npm run api:generate`用本地`openapi-typescript`生成，`api:check`重生成到临时文件后逐字比较，不自行访问外网schema。

```ts
test('OIDC session keeps tokens out of browser storage', async ({ page, context }) => {
  await login(page, 'editor-a');
  await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  expect(await page.evaluate(() => localStorage.getItem('access_token'))).toBeNull();
  const session = (await context.cookies()).find(c => c.name === 'lzo_session');
  expect(session?.httpOnly).toBe(true);
  expect(session?.secure).toBe(true);
  await logout(page);
  await expect(page.getByRole('button', { name: '登录' })).toBeVisible();
});
```

- [ ] **2. 跑红灯。** `npx playwright test tests/e2e/workbench/session-shell.spec.ts`；预期缺少会话页面/登录导航断言失败，Keycloak 与 API 本身应健康。
- [ ] **3. 建立实际web构建并实现客户端/会话边界。** 使用F01已锁Node 24精确补丁，不指定另一个主版本；增加Vite/TS/Vitest/Playwright配置和锁文件，生成SDK脚本。代理把页面、API、身份回调和本地地图资源放同源内网HTTPS；`credentials: include`且写请求遵循 F03 CSRF；Token不进入URL、local/sessionStorage、前端日志或fixture。
- [ ] **4. 实现原创 shell。** 本地字体与图标、中文标题及辅助英文术语、合成数据标记、折叠菜单、固定顶栏与独立滚动区域；导航先以已交付页启用，其余明确列为待实现而非假按钮。
- [ ] **5. 实现会话续期测试和客户端逻辑。** 同一时间只进行一次刷新；刷新失败清空 TanStack Query 的用户数据，显示重新登录；401 后只重试安全读取，写命令由用户确认且复用幂等键。

```ts
expect(await page.evaluate(() => sessionStorage.getItem('refresh_token'))).toBeNull();
await page.getByRole('link', { name: '跳到主要内容' }).focus();
await page.keyboard.press('Enter');
await expect(page.getByRole('main')).toBeFocused();
```

- [ ] **6. 补关键负例。** 回调无效 state/nonce 由 F03 拒绝且 UI 显示可恢复错误；两个标签页退出后另页敏感数据清理；键盘 Tab、ARIA 名、768px与1280px布局无不可达控件。
- [ ] **6a. 明确缓存生命周期。** 查询key包含稳定用户ID和authorization_version；同源广播退出/授权版本变化，清除旧用户查询与选中详情；客户端仅做呈现，所有后续请求仍由后端按当前权限重验。
- [ ] **7. 跑绿灯与生成检查。** 执行步骤2命令、`npm run test -- src/api/client.test.ts`、`npm run typecheck`、`npm run build`、`npm run api:check`；所有断言通过且构建资源无外链CDN。
- [ ] **8. 小提交。** 显式暂存上述U01文件及锁文件；不执行整个web目录或`git add .`，避免带入其他任务未完成内容。
- [ ] `git add package.json package-lock.json playwright.config.ts scripts/export_openapi.py scripts/seed_workbench.py tests/e2e/support/workbench.ts tests/e2e/workbench/session-shell.spec.ts infra/compose/compose.yaml infra/proxy/nginx.conf contracts/openapi.json`；再按Files逐个暂存web文件。
- [ ] `git commit -m "feat(web): add real OIDC session and Chinese workbench shell"`。

### U02：Ontology Studio、草稿恢复与发布

**Files**
- Create: `apps/web/src/features/studio/StudioPage.tsx`、`ClassTree.tsx`、`SchemaCanvas.tsx`、`Inspector.tsx`。
- Create: `apps/web/src/features/studio/ShapeEditor.tsx`、`DraftCommands.ts`、`PublishPanel.tsx`。
- Create: `apps/web/src/features/governance/ApprovalsPanel.tsx`；先提供真实`/governance`审批入口，U04/U06在此基础扩展。
- Modify: `apps/web/src/app/router.tsx`、`apps/web/src/api/generated.ts`、`scripts/seed_workbench.py`。
- Test: `apps/web/src/features/studio/DraftCommands.test.ts`、`tests/e2e/workbench/studio.spec.ts`。
- Modify: `tests/e2e/support/workbench.ts`；增加真实jobs轮询助手，错误/超时抛出且附job ID，不把202算完成。

**Interfaces**
- S02 owns `GET/POST /schema/drafts`、`GET/PATCH /schema/drafts/{id}`、`GET /schema/drafts/{id}/inheritance`、`GET /schema/drafts/{id}/form-schema`。
- S02 owns `POST /schema/drafts/{id}/validate`、`POST /schema/drafts/{id}/submit`、`GET /schema/releases`、`GET /schema/releases/{id}/diff?base_release_id=`、`POST /schema/releases`。
- S02 owns `GET /schema/releases/{id}`、`GET /schema/releases/{id}/form-schema?class_iri=`；operationIds `getSchemaRelease/getReleasedFormSchema`，直接消费生成响应。
- F06 owns `GET/POST /approvals`、`GET /approvals/{id}`、`POST /approvals/{id}/decisions`；F05 owns `/jobs/{id}`；F03 owns `/history`与diff；U02在发布面板和ApprovalsPanel连接这些端点。
- Produce: `StudioPage(): React.JSX.Element`；`DraftCommands.ts`保存S02生成的`UpdateDraftRequest(document: SchemaDocument)`不可变快照用于撤销/重做；PATCH提交完整document与If-Match，不新增REST操作DTO。

- [ ] **1. 写新增子类、继承、独立审核发布的失败测试。**

```ts
test('new device type is a governed schema change', async ({ page, browser }) => {
  await login(page, 'editor-a');
  await page.goto('/ontology/studio');
  await page.getByRole('button', { name: '新建类' }).click();
  await page.getByLabel('中文名称').fill('多场景氨氮传感器');
  await page.getByLabel('父类').selectOption({ label: 'Sensor' });
  await page.getByRole('button', { name: '保存草稿' }).click();
  await expect(page.getByText('继承属性')).toBeVisible();
  await expect(page.getByText('高风险 · 需要其他人员审核')).toBeVisible();
  await page.getByRole('button', { name: '提交审核' }).click();
  await expect(page.getByRole('button', { name: '审核通过' })).toBeDisabled();
  const reviewer = await createNamedContext(browser, 'reviewer-a');
  await reviewer.page.goto('/governance');
  await reviewer.page.getByRole('button', { name: '审核通过' }).first().click();
  await expect(reviewer.page.getByText('已批准')).toBeVisible();
  await page.reload();
  await page.getByRole('button', { name: '发布已批准版本' }).click();
  await expect(page.getByText('发布完成', { exact: true })).toBeVisible();
});
```

- [ ] **2. 跑红灯。** `npx playwright test tests/e2e/workbench/studio.spec.ts`；预期Studio操作或发布状态断言失败。
- [ ] **3. 实现类树、XYFlow和Inspector。** Schema与实例图完全分开；可搜索类、父类与来源包；画布线形区分继承与业务关系，拖线先确认方向再写草稿；键盘/列表能完成同样编辑。
- [ ] **4. 实现字段与Shape编辑。** 数据类型、单位、枚举、必填、基数、范围、模式、端点、所有权和UI控件分别保存；展示继承来源和受影响实例，不将UI元数据写成OWL语义。
- [ ] **5. 实现草稿操作栈与恢复。** 服务端草稿revision为基线；撤销/重做形成新的草稿更新；未提交操作留在内存并提示离开；不把localStorage草稿当作服务端已保存。

```ts
await page.reload();
await expect(page.getByText('多场景氨氮传感器')).toBeVisible();
await page.getByLabel('中文名称').fill('未提交名称');
// 第二个上下文先提交同一草稿；本页随后提交必须收到409。
await page.getByRole('button', { name: '保存草稿' }).click();
await expect(page.getByRole('alert')).toContainText('版本冲突');
await expect(page.getByLabel('中文名称')).toHaveValue('未提交名称');
```

- [ ] **6. 实现validate→impact/diff→submit→approval→publish。** 低风险文字修改仍校验审计；新增结构必走异人审核；调用发布返回202后显示Job状态，只有有效版本读回才显示完成；失败可重试，不生成假发布成功。
- [ ] **7. 增加预览失效负例。** 批准后修改草稿应失效，重新校验审核；父类新增必填字段显示违规数量；循环继承和不支持Shape构造显示机器错误及中文说明，不静默丢失内容。
- [ ] **8. 跑绿灯。** 重跑步骤2与 `npm run test -- src/features/studio/DraftCommands.test.ts`，再执行共享类型/构建检查；断网草稿不得标“已保存”，重开仍能恢复最后服务端草稿。
- [ ] **9. 小提交。** `git add apps/web/src/features/studio apps/web/src/features/governance/ApprovalsPanel.tsx apps/web/src/app/router.tsx apps/web/src/api/generated.ts scripts/seed_workbench.py tests/e2e/support/workbench.ts tests/e2e/workbench/studio.spec.ts`。
- [ ] `git commit -m "feat(studio): add governed schema editing and recoverable drafts"`。

### U03：动态实例表单、关系、来源与并发修改

**Files**
- Create: `apps/web/src/features/objects/RegistryPage.tsx`、`DynamicForm.tsx`、`ObjectDrawer.tsx`。
- Create: `apps/web/src/features/objects/LinkEditor.tsx`、`ProvenancePanel.tsx`、`FieldOwnership.tsx`。
- Modify: `apps/web/src/app/router.tsx`、`apps/web/src/api/generated.ts`、`scripts/seed_workbench.py`。
- Test: `apps/web/src/features/objects/DynamicForm.test.tsx`、`tests/e2e/workbench/objects.spec.ts`。

**Interfaces**
- S03 owns `GET/POST /objects`、`GET/PATCH /objects/{id}`、`POST /objects/{id}/deactivate`、`GET /objects/{id}/form-schema`。
- 对象/关系写入返回S03 `WriteReceipt`，从`revision.resource_id`取得对象UUID，再GET详情；不能假设POST响应顶层有`id`，不能把pending当图谱已同步。
- S02 owns `GET /schema/releases/{id}/form-schema?class_iri=` before an object exists; select published class/release first, then render this response.
- S03 owns `GET/POST /links`、`POST /links/{id}/withdraw`；candidate acceptance/rejection uses I02 `POST /conflicts/{id}/rulings`，不建立第二条审核流；`POST /validation` returns field/Shape errors.
- S03 owns `GET /objects/{id}/assertions`（operationId `listObjectAssertions`）；I06 owns `GET /sources/provenance?entity_id=&assertion_id=`（`getSourceProvenance`）；F03 owns历史，I01/F04 own证据metadata/download。
- Produce: `DynamicForm(): React.JSX.Element` with backend-generated fields; `ObjectDrawer(): React.JSX.Element` reused by registry,graph,map.

- [ ] **1. 写三个场景复用表单与非法关系失败测试。**

```ts
for (const label of ['农田', '水塘', '果园']) {
  test(`${label}: create through published metadata`, async ({ page }) => {
    await login(page, 'editor-a');
    await page.goto('/objects');
    await page.getByRole('button', { name: '创建对象' }).click();
    await page.getByLabel('对象类型').selectOption({ label });
    await expect(page.getByLabel('数据性质')).toHaveValue('synthetic');
    await page.getByLabel('显示名称').fill(`测试${label}`);
    await page.getByRole('button', { name: '创建' }).click();
    await expect(page.getByRole('heading', { name: `测试${label}` })).toBeVisible();
    await expect(page.getByText('演示数据', { exact: true }).first()).toBeVisible();
  });
}
```

- [ ] **2. 跑红灯。** `npx playwright test tests/e2e/workbench/objects.spec.ts`；预期缺少动态创建/关系校验界面。
- [ ] **3. 实现列表与动态表单。** 服务端过滤分页按类型、来源、数据性质、状态、区域；文本、数值、日期、枚举、单位、对象引用和几何引用按元数据渲染；不写设备/场景专用表单分支。
- [ ] **4. 实现关系与权属。** 对象引用框按端点类型和可见范围查API；来源字段/派生字段只读，平台自有字段可编辑；不允许仅靠前端过滤阻止非法关系。

```ts
await expect(page.getByLabel('来源设备状态')).toBeDisabled();
await page.getByRole('button', { name: '添加关系' }).click();
await page.getByLabel('关系类型').selectOption({ label: '监测' });
await expect(page.getByRole('option', { name: 'Shipment-001' })).toHaveCount(0);
// API端点拒绝422由S03真实集成负例验证；U03测试返回错误定位与刷新后无幽灵边。
await page.reload();
await expect(page.getByText('Shipment-001')).toHaveCount(0);
```

- [ ] **5. 实现详情来源/证据/历史。** 展示原值、标准化候选、采纳值和派生依据；区分业务时间/系统时间；PDF/Word/图片通过I01证据上传，关联实体/断言并展示审核状态和摘要；下载走F04权限路由，责任链从I06读取。历史详情读F03`GET /history/{id}/revisions/{revision}`，不读当前值冒充历史。
- [ ] **6. 实现乐观锁与停用。** 保存使用If-Match；409展示当前修订与未提交输入，不自动覆盖；停用先展示影响，确认后命令入审计，UI不提供硬删除。
- [ ] **7. 补动态扩展负例。** U02发布新设备子类后不改页面即可录入；未发布类型不可创建；422定位字段/Shape；共享设备显示一个ID和一个主管理归属，服务区域可多值且不暗示编辑权。
- [ ] **8. 跑绿灯。** 重跑步骤2、`npm run test -- src/features/objects/DynamicForm.test.tsx`及共享检查；服务重启后新实例、关系、来源和版本仍能读回。
- [ ] **9. 小提交。** `git add apps/web/src/features/objects apps/web/src/app/router.tsx apps/web/src/api/generated.ts scripts/seed_workbench.py tests/e2e/workbench/objects.spec.ts`。
- [ ] `git commit -m "feat(objects): add metadata forms links and provenance"`。

### U04：来源映射、冲突裁决与规则重算

**Files**
- Create: `apps/web/src/features/sources/SourcesPage.tsx`、`MappingEditor.tsx`、`ImportPreview.tsx`、`SyncSchedule.tsx`。
- Create: `apps/web/src/features/governance/ConflictReview.tsx`、`RuleEditor.tsx`、`ImpactPreview.tsx`、`RecalculationPanel.tsx`。
- Create: `apps/web/src/shared/JobStatus.tsx`。
- Modify: `apps/web/src/app/router.tsx`、`apps/web/src/api/generated.ts`、`scripts/seed_workbench.py`。
- Test: `tests/e2e/workbench/sources-governance.spec.ts`、`apps/web/src/features/governance/ImpactPreview.test.tsx`。

**Interfaces**
- I02 owns `GET/POST /sources`（`listSources/createSource`）、`PUT /sources/{id}/mapping`（`saveSourceMapping`）；I03 owns `PUT /sources/{id}/connection`（`configureSourceConnection`）。
- I03 owns `PUT /sources/{id}/credentials`（`setSourceCredentials`）；输入使用生成的writeOnly秘密字段，响应仅CredentialRef；UI不将密码写入草稿、浏览器存储或审计，修改凭证要求`sources.credentials.manage`。
- I03 owns `POST /sources/{id}/test`（`testSourceConnection`）、`PUT /sources/{id}/schedule`（`configureSourceSchedule`）；I04 owns `POST /imports/preview`（`previewImport`）、`POST /imports/apply`（`applyImport`）。
- I04 owns `GET /imports/{id}`（`getImport`）、`GET /imports/{id}/rows`（`listImportRows`）；I02 owns `GET /conflicts`（`listConflicts`）、`POST /conflicts/{id}/rulings`（`resolveConflict`）。
- I04 owns `GET /imports/previews/{id}`读取持久预检报告；大预检202后读取F05 JobView.result_ref，再请求该路由。规则影响同样按result_ref读取I05 `GET /rules/impact/{id}`，不依赖浏览器保留旧报告。
- I04 `ImportSelection`新增`semantic_format: 'turtle'|'json-ld'|None`和`semantic_kind: 'schema'|'shapes'|'instances'|None`；U04直接使用生成类型，不另造语义导入DTO。
- I02 owns `POST /identity/merges/preview`（`previewIdentityMerge`）：生成`MergePreviewCommand(canonical_id,duplicate_ids)`→MergeProposal；`POST /identity/merges`（`mergeIdentities`）：生成`MergeCommand(proposal_id,approval_id)`→RevisionRef，复用F06独立审批。
- I05 owns `POST /rules/versions`（`createRuleVersion`）、`POST /rules/impact`（`previewRuleImpact`）、`POST /rules/{id}/publish`（`publishRules`）、`POST /recalculations`（`startRecalculation`）。
- I05 owns `POST /recalculations/{id}/rollback-preview`（`previewRollback`）、`POST /recalculations/{id}/rollback`（`rollbackRecalculation`）；F05 owns `POST /jobs/{id}/pause`、`/resume`、`/retry`，F06 owns approvals。
- I01 owns `POST /artifacts/evidence`（`createEvidence`）、`GET /artifacts/evidence/{id}`（`getEvidence`）；F04 owns `POST /artifacts`、`GET /artifacts/{version_id}`、`GET /artifacts/{version_id}/content`。
- I02 owns `GET /sources/{id}`（`getSource`）、`GET /sources/{id}/mapping`（`getSourceMapping`）；I03 owns `GET /sources/{id}/connection`（`getSourceConnection`）、`GET /sources/{id}/schedule`（`getSourceSchedule`）。
- I02 owns `GET /conflicts/{id}`（`getConflict`）、`GET /conflicts/{id}/rulings`（`listConflictRulings`）。
- I05 owns `GET /rules/versions`（`listRuleVersions`）、`GET /rules/versions/{id}`（`getRuleVersion`）、`GET /rules/impact/{id}`（`getRuleImpact`）、`GET /recalculations/{id}`（`getRecalculation`）。
- 刷新页面读取上述持久状态，不由浏览器回填假任务；无 `/ui/*` shadow API 或浏览器权威规则执行。
- Produce: `JobStatus({jobId}): React.JSX.Element` using generated `/jobs/{id}` response; `ImpactPreview(): React.JSX.Element` for immutable report/baseline.

- [ ] **1. 写真实多源预检与裁决失败测试。** 从PG/MySQL和CSV/XLSX/JSON/GeoJSON选择真实测试来源；读取schema后配置列→property IRI、单位、空值、键、范围及身份匹配，preview不得创建对象。

```ts
test('preview is read-only and stale ruling cannot silently win', async ({ page }) => {
  await login(page, 'data-engineer-a');
  await page.goto('/sources');
  await page.getByRole('button', { name: '预检' }).click();
  await expect(page.getByText('尚未写入正式数据')).toBeVisible();
  await expect(page.getByRole('table', { name: '逐字段预检结果' })).toBeVisible();
  await page.getByRole('button', { name: '应用导入' }).click();
  await expect(page.getByText('已受理，等待处理')).toBeVisible();
  await page.goto('/quality');
  await expect(page.getByText('原始值')).toBeVisible();
  await expect(page.getByText('裁决需要重新评估')).toBeVisible();
});
```

- [ ] **2. 跑红灯。** `npx playwright test tests/e2e/workbench/sources-governance.spec.ts`；预期来源向导/裁决界面缺失。
- [ ] **3. 实现接入向导与同步配置。** PG/MySQL只读连接；凭证写入后掩码且不回显；文件工作表/表头/记录路径可选；全量、手动增量、定时增量明确区分；不可提供可靠游标时显示快照对账语义。
- [ ] **4. 实现正式应用与任务明细。** 展示新增/更新/失效/冲突/错误统计和逐行定位，预览绑定输入摘要及配置版本；apply前再次校验版本；失败项可查看和定位，部分成功不能显示全部完成。
- [ ] **4a. 实现语义文件入口。** Turtle/JSON-LD上传后填写ImportSelection的semantic_format/kind并经I04统一preview/apply；报告区分Schema/Shape草稿结果与实例流水线结果，提供实际草稿/批次链接。Schema/Shape进入S02草稿与审批，实例经过身份/SHACL/来源治理。超范围公理和远程context错误必须定位；自动抽取只说明后续接口，不提供假“AI完成”按钮。
- [ ] **4b. 补语义导入与异步结果E2E。** 在`tests/e2e/workbench/sources-governance.spec.ts`上传同一测试包的Turtle和JSON-LD版本，选择schema/shapes并应用后断言出现本体草稿链接且未切换已发布版本；preview本身仍只读。选择instances后展示SHACL/对齐及逐行结果；大预检刷新页面后仍能从job.result_ref读取报告。

```ts
await page.getByLabel('语义文件格式').selectOption('turtle');
await page.getByLabel('语义内容用途').selectOption('schema');
await page.getByRole('button', { name: '预检' }).click();
await expect(page.getByText('尚未写入正式数据')).toBeVisible();
await page.getByRole('button', { name: '应用导入' }).click();
await expect(page.getByRole('link', { name: '查看本体草稿' })).toBeVisible();
await page.reload();
await expect(page.getByRole('table', { name: '逐字段预检结果' })).toBeVisible();
```
- [ ] **5. 实现冲突裁决。** 并列显示来源原值、候选值、当前采纳值、证据与相关版本；采纳、拒绝、维持当前值必须填写理由；身份合并和重要权属变更交独立审批，旧裁决依赖变化显示复核状态。
- [ ] **5a. 实现身份合并可审阅入口。** 在身份冲突详情选择规范对象和重复对象，调用merge preview展示双方范围、保留UUID/IRI、旧IRI别名、来源绑定/历史及字段冲突；不确定身份不得一键执行。提交F06高风险审批后，仅凭同一proposal与approval调用merge；预览过期或任一对象无权时禁止继续，字段冲突保留待裁决而非强制覆盖。
- [ ] **5b. 补合并E2E。** 同一`tests/e2e/workbench/sources-governance.spec.ts`中用独立审核上下文批准，断言规范ID不变、旧IRI可追踪、冲突字段仍待审核；提交者自审、只拥有一方区域、预览后另一方更新均失败且无半次合并。

```ts
await page.getByRole('button', { name: '预览实体合并' }).click();
await expect(page.getByText('保留规范对象身份')).toBeVisible();
await expect(page.getByText('冲突字段将继续等待裁决')).toBeVisible();
await page.getByRole('button', { name: '提交合并审核' }).click();
await expect(page.getByRole('button', { name: '执行合并' })).toBeDisabled();
```
- [ ] **6. 实现规则和影响报告。** 类型转换、单位、条件、组合、稳定键和优先级用结构化编辑；预览含实例、字段、关系、推理、仍适用裁决、需复核裁决及新增冲突；预览不写正式事实。
- [ ] **7. 实现历史重算任务。** 发布仅影响新批次，历史重算需选范围并独立批准；展示暂停/继续/失败项重试/补偿回滚。规则版本切回和结果回滚分成两个命令，执行前显示不同影响。

```ts
await page.getByRole('button', { name: '暂停重算' }).click();
await expect(page.getByText('已暂停')).toBeVisible();
await page.getByRole('button', { name: '重试失败项' }).click();
await expect(page.getByText('仅重试失败项')).toBeVisible();
await page.getByRole('button', { name: '申请结果回滚' }).click();
await expect(page.getByText('需要其他人员审核')).toBeVisible();
```

- [ ] **8. 补过期预览负例。** 预检后修改mapping或权限，再apply必须提示重做预览；重算遇到新实质冲突不得沿用旧裁决；旧任务结果不能覆盖新数据；停止轮询的页面重新打开能从服务端恢复任务状态。
- [ ] **9. 跑绿灯。** 重跑步骤2、`npm run test -- src/features/governance/ImpactPreview.test.tsx`及共享检查；断开一个外部来源只改变其状态，其他来源/页面仍可用。
- [ ] **10. 小提交。** `git add apps/web/src/features/sources apps/web/src/features/governance apps/web/src/shared/JobStatus.tsx apps/web/src/app/router.tsx apps/web/src/api/generated.ts scripts/seed_workbench.py tests/e2e/workbench/sources-governance.spec.ts`。
- [ ] `git commit -m "feat(governance): connect imports rulings and controlled recalculation"`。

### U05：受控图谱/SPARQL与在线离线地图

**Files**
- Create: `apps/api/src/lzo/maps/schemas.py`、`models.py`、`service.py`、`router.py`、`tiles.py`、`apps/api/alembic/versions/0301_map_resources.py`。
- Create: `apps/api/tests/integration/test_map_resources.py`、`test_map_tiles.py`。
- Create: `scripts/generate_demo_map.py`、`tests/fixtures/maps/manifest.json`、`tests/fixtures/maps/style.json`、`tests/fixtures/maps/LICENSES.md`；生成可重现的小型MBTiles/PMTiles样包，glyph/sprite固定摘要登记在manifest，D02原样打包。
- Create: `apps/web/src/features/graph/GraphPage.tsx`、`SparqlPanel.tsx`、`ProofPanel.tsx`。
- Create: `apps/web/src/features/maps/MapPage.tsx`、`ResourceManager.tsx`、`styleResolver.ts`。
- Create: `apps/web/src/shared/selection.tsx`、`apps/web/public/assets/maps/LICENSES.md`。
- Modify: `apps/api/src/lzo/main.py`、`apps/web/src/app/router.tsx`、`apps/web/src/api/generated.ts`、`scripts/seed_workbench.py`。
- Test: `tests/e2e/workbench/graph-map.spec.ts`、`offline-map.spec.ts`、`apps/web/src/features/maps/styleResolver.test.ts`。

**Interfaces**
- S03 owns `GET /geometry?bbox=&scope_id=&cursor=`→GeoFeatureCollection、`GET/PUT /geometry/{object_id}`；Feature的`properties.object_id`绑定稳定UUID，与`/objects/{id}`一致，不另造`entity_id` REST字段。
- S05 owns `GET /graph/neighborhood?resource_id=&hops=&cursor=`、`POST /query/sparql`、`POST /query/exports`；S04 owns `GET /graph/proofs/{assertion_id}`；页面URL的`focus`转换为查询的`resource_id`。
- U05 owns `GET/POST /maps/resources`、`GET/PATCH /maps/resources/{id}` and defines request/response Pydantic schemas there; UI derives generated types.
- U05 owns `GET /maps/resources/{id}/style.json`、`GET /maps/resources/{id}/tiles/{z}/{x}/{y}`、`GET /maps/resources/{id}/archive`.
- U05 owns `GET /maps/resources/{id}/assets/{asset_path}` for registered glyph/sprite/image files（FastAPI实现用`{asset_path:path}`接收清单内多层路径）；无任意文件路径或远程URL代理。
- New resource writes use F02 revisions/outbox/audit, F03 permissions, F04 BlobRef/ArtifactStore; map package upload reuses F04 `POST /artifacts`，不冒充PDF证据上传。
- U05定义maps专有Pydantic `MapResourceCreate/MapResourcePatch/MapResourceView`，不重复其他任务DTO；前端仍只导入OpenAPI生成类型。
- `MapResourceCreate`字段：`title: str`、`kind: Literal['online','mbtiles','pmtiles']`、`tile_kind: Literal['raster','mvt']`、`artifact_version_id: UUID|None`、`style_version_id: UUID|None`、`online_url_template: str|None`、`bbox: tuple[float,float,float,float]`、`min_zoom/max_zoom: int`、`attribution/license: str`、`asset_versions: dict[str,UUID]`、`owner_scope_id: UUID`。
- `MapResourceView`含上述非秘密字段与`id/revision/validation_status`；Patch为允许编辑字段的可选版本，不允许改id；鉴权后选择资源，不能让同摘要包自动共享权限。
- Produce in `service.py`: `register_map_resource(uow: UnitOfWork,actor: ActorContext,payload: MapResourceCreate)->RevisionRef`；`read_tile(actor: ActorContext,resource_id: UUID,z:int,x:int,y:int)->bytes`。
- Produce in `router.py`: `open_archive_range(actor: ActorContext,resource_id: UUID,range_header: str|None)->StreamingResponse`，从ArtifactStore只读可seek流按范围分块读取，不把整包放入内存。
- operationIds固定：`listMapResources/createMapResource/getMapResource/updateMapResource/getMapStyle/getMapTile/getMapArchive/getMapAsset`；地图包默认无授权不能读，在线底图凭证不写到公开style。

- [ ] **1. 写后端地图资源失败测试。**

本文件定义`client` fixture复用F01 `api_client`；`map_resource` fixture调用F04与maps服务注册专用合法PMTiles测试包；`viewer` fixture在同一client创建真实F03会话和授权，`viewer.headers`仅测试CSRF等请求header，`viewer.revoke()`调用真实授权撤销服务；不靠mock返回206。

```python
def test_pmtiles_range_is_exact_and_authorized(client, map_resource, viewer):
    response = client.get(f"/api/v1/maps/resources/{map_resource.id}/archive",
                          headers={**viewer.headers, "Range": "bytes=0-127"})
    assert response.status_code == 206
    assert response.headers["Accept-Ranges"] == "bytes"
    assert len(response.content) == 128
    assert response.headers["Content-Range"].startswith("bytes 0-127/")
    viewer.revoke()
    assert client.get(f"/api/v1/maps/resources/{map_resource.id}/archive",
                      headers=viewer.headers).status_code == 404
```

- [ ] **2. 跑红灯。** `pytest tests/integration/test_map_resources.py tests/integration/test_map_tiles.py -q`；预期maps路由未实现。
- [ ] **3. 实现资源登记与服务。** 先生成具有多缩放层级、可实际平移缩放的合成样包；glyph/sprite资产记录许可证与摘要，不以静态截图替代地图。上传后校验bbox/zoom、格式、大小与摘要，未知格式明报不支持。持久资源经F04 BlobRef读取，不接受客户端路径；MBTiles用只读SQLite和参数化z/x/y查询，正确转换XYZ/TMS行号及内容编码。迁移0301串行接现有单一head，不改写已执行迁移。
- [ ] **4. 实现PMTiles与本地资产。** Range返回206/正确Content-Range；越界416，不支持范围时不伪造206；ETag随版本，所有有权限内容使用`Cache-Control: private,no-store`并在每次Range/瓦片请求重验权限，不让旧浏览器缓存绕过撤权。离线资源的样式资产只允许清单内相对路径；阻止`..`、未知绝对URL及glyph/sprite隐式外网请求。在线底图仅可使用管理员明确登记的服务模板，含服务端密钥时通过固定资源适配器访问，不提供任意URL转发。
- [ ] **5. 补资源测试并跑绿灯。** 检查MBTiles栅格/MVT、缺失瓦片/越界缩放提示、包损坏、非法Range、路径穿越、未授权读取；重跑步骤2及API生成；首次小提交仅maps后端和测试。
- [ ] `git add apps/api/src/lzo/maps apps/api/src/lzo/main.py apps/api/alembic/versions/0301_map_resources.py apps/api/tests/integration/test_map_resources.py apps/api/tests/integration/test_map_tiles.py scripts/generate_demo_map.py tests/fixtures/maps contracts/openapi.json`。
- [ ] `git commit -m "feat(maps): serve authorized MBTiles PMTiles and local assets"`。
- [ ] **6. 写浏览器交叉定位与离线失败测试。**

```ts
test('map graph registry preserve one identity', async ({ page }) => {
  await login(page, 'viewer-a');
  await page.goto('/geo');
  await page.getByRole('button', { name: '对象列表' }).click();
  await page.getByRole('button', { name: '演示水塘001' }).click();
  const id = await page.getByTestId('entity-id').innerText();
  await page.getByRole('link', { name: '查看关系' }).click();
  await expect(page).toHaveURL(new RegExp(`focus=${id}`));
  await expect(page.getByTestId('entity-id')).toHaveText(id);
});
```

- [ ] **7. 跑红灯。** `npx playwright test tests/e2e/workbench/graph-map.spec.ts tests/e2e/workbench/offline-map.spec.ts`；预期图/地图页面或本地底图未完成。
- [ ] **8. 实现Cytoscape图与查询。** 局部展开、方向中文标签、候选虚线/采纳实线、来源和时间过滤、节点预算与已展示数量；只渲染受权结果，Schema不进入实例图。SPARQL只走受控路由，拒绝/超预算显示错误；查询导出等待Job并经权限下载。
- [ ] **9. 实现ProofPanel与共享选中状态。** 使用URL `focus=<entity_id>`联动ObjectDrawer和地图，证明展开由API读取当前授权依据，不在浏览器重推受限节点；重复打开不复制实体。
- [ ] **10. 实现MapLibre与资源管理。** 在线配置、MBTiles、PMTiles切换；资源列表、上传、校验、登记、选择都是真实API；业务几何独立图层，农田/水塘/果园可筛选，显示CRS来源、比例尺、数据性质、更新时间、同步状态及覆盖不足提示。
- [ ] **11. 验证真正离线。** 拦截所有非内网请求、清浏览器缓存并重启；同源登录/地图/对象/图谱可用，离线本地glyph和sprite真实被读取；在线地图失败可切本地，WebGL不可用提供对象列表而不假称地图成功。

```ts
// 在新的BrowserContext中登录后，加载地图前注册；所有生产资源走同源内网代理。
const appOrigin = new URL(page.url()).origin;
const externalRequests: string[] = [];
const localAssetRequests: string[] = [];
page.on('request', request => {
  if (!request.url().startsWith('http')) return;
  if (new URL(request.url()).origin !== appOrigin) externalRequests.push(request.url());
  else localAssetRequests.push(request.url());
});
await page.route('**/*', route => new URL(route.request().url()).origin === appOrigin
  ? route.continue() : route.abort('internetdisconnected'));
await page.goto('/geo');
await expect(page.getByText('离线底图已加载')).toBeVisible();
expect(externalRequests).toEqual([]);
expect(localAssetRequests.some(url => url.includes('/assets/'))).toBe(true);
await expect(page.getByRole('button', { name: '演示果园001' })).toBeVisible();
// 已登录viewer在另一管理员撤权后刷新邻域/地图，不能残留详情与可猜计数。
await expect(page.getByText('受限设备名称')).toHaveCount(0);
```

- [ ] **12. 跑绿灯。** 重跑步骤7、`npm run test -- src/features/maps/styleResolver.test.ts`及共享检查；保存三场景截图、请求清单和真实地图加载证据给D02复用。
- [ ] **13. 第二次小提交。** `git add apps/web/src/features/graph apps/web/src/features/maps apps/web/src/shared/selection.tsx apps/web/public/assets/maps apps/web/src/app/router.tsx apps/web/src/api/generated.ts scripts/seed_workbench.py tests/e2e/workbench/graph-map.spec.ts tests/e2e/workbench/offline-map.spec.ts`。
- [ ] `git commit -m "feat(explore): connect graph SPARQL and offline agricultural maps"`。

### U06：用户与范围授权、全类型历史、健康和整体前端验证

**Files**
- Create: `apps/web/src/features/access/AccessPage.tsx`、`UserGroups.tsx`、`GrantEditor.tsx`、`SharedObjectGrants.tsx`。
- Create: `apps/web/src/features/history/HistoryPage.tsx`、`RevisionDiff.tsx`。
- Create: `apps/web/src/features/system/OverviewPage.tsx`、`HealthPanel.tsx`、`SettingsPage.tsx`。
- Modify: `apps/web/src/app/router.tsx`、`apps/web/src/api/generated.ts`。
- Test: `tests/e2e/workbench/access-history.spec.ts`、`tests/e2e/workbench/accessibility.spec.ts`。
- Create: `docs/design/workbench-navigation.md`、`docs/verification/workbench-api-coverage.md`。

**Interfaces**
- F03 owns `GET/POST /users`、`POST /users/invitations`、`PATCH /users/{id}`；`GET/POST /groups`、`PATCH /groups/{id}`、`PUT /groups/{id}/members`。
- F03 owns `GET/POST /roles`、`PATCH /roles/{id}`、`GET/POST /grants`、`PATCH/DELETE /grants/{id}`；DELETE追加撤销记录，不抹掉授权历史。
- F06 owns `GET/POST /approvals`、`GET /approvals/{id}`、`POST /approvals/{id}/decisions`；F03 owns `GET /history?resource_id=&resource_kind=&actor_id=&task_id=&recorded_from=&recorded_to=&cursor=`、`GET /history/{id}/revisions/{revision}`、`GET /history/{id}/diff?from_revision=&to_revision=`。
- F01 owns `/health/live`、`/health/ready`；F05 owns `GET /health/overview`与`GET /jobs`；U06显示服务响应，不查询数据库。
- Produce: `AccessPage(): React.JSX.Element`、`RevisionDiff(): React.JSX.Element`、`OverviewPage(): React.JSX.Element`；all permissions derived from full grant records, not union of separate role and region arrays.
- `/settings` edits only implemented settings via responsible domain routes: map resource→U05,namespace/schema→S02,roles/grants→F03; no fake theme/plugin administration.

- [ ] **1. 写授权不交叉组合和共享撤销失败测试。**

```ts
test('editor in A plus viewer in B cannot edit B', async ({ page }) => {
  await login(page, 'editor-a-viewer-b');
  await page.goto('/objects');
  await page.getByRole('button', { name: '区域B水塘' }).click();
  await expect(page.getByRole('button', { name: '编辑对象' })).toBeDisabled();
  await expect(page.getByText('主管理归属')).toBeVisible();
  await expect(page.getByText('共享查看权限')).toBeVisible();
  // F03/S03真实API负例必须403；此页面测试不能替代后端授权测试。
});
```

- [ ] **2. 跑红灯。** `npx playwright test tests/e2e/workbench/access-history.spec.ts`；预期授权编辑器或历史差异页未完成。
- [ ] **3. 实现用户/组/完整授权项。** 管理员邀请支持内网邮件/一次性激活，失败不谎报送达；用户可多个组，每授权项绑定角色/操作、区域及对象。共享查看/使用/特定操作分别选择，服务关系不自动变成授权。
- [ ] **4. 实现授权变更风险流程。** 跨域扩权提交影响报告与独立审批，普通共享调整仍需授权管理权；不提供实际归属转移按钮，仅明确其后续接口边界；撤权后清缓存并使进行中的读取/导出重新授权。
- [ ] **5. 实现全类型历史。** 定义、字段、关系、几何、来源、规则、裁决、推理均按资源/版本/时间/操作者/任务筛选；diff展示新增/修改/失效/撤回，责任人为稳定用户或任务+发起人；几何可对照，推理可展开当时依据。

```ts
await page.goto('/governance/history');
await page.getByLabel('资源类型').selectOption('geometry');
await page.getByRole('button', { name: '对比所选版本' }).click();
await expect(page.getByText('修改前')).toBeVisible();
await expect(page.getByText('修改后')).toBeVisible();
await expect(page.getByText('责任人')).toBeVisible();
await expect(page.getByRole('button', { name: '编辑历史' })).toHaveCount(0);
```

- [ ] **6. 实现总览/健康/设置。** 数量来自授权聚合，状态来自真实服务和任务指标；依赖故障显示最后更新时间、重试和可用范围，外部可选来源失败不掩盖核心可用；后续模块只明确说明，不显示假成功。
- [ ] **7. 补当前权限和完整性负例。** 撤权后历史/证据/导出/证明不可访问；历史数据更正需追加新修订；服务器完整性检查异常可见，界面不得允许管理员删除审计；高风险自审在按钮与直接API两处均被拒绝。
- [ ] **8. 完成全前端a11y检查。** 每页键盘操作、焦点恢复、对比度、ARIA、读屏状态、错误定位；图/地图有列表替代，窄屏无丢失关键提交操作；页面断网不得保留假成功状态。
- [ ] **9. 跑绿灯与整体构建。** 重跑步骤2及 `npx playwright test tests/e2e/workbench/accessibility.spec.ts`、全部workbench测试；`npm run typecheck`、`npm run build`、`npm run api:check`全部通过。
- [ ] **10. 记录交付证据。** 在API覆盖表逐一列按钮→method/path→所属任务→成功/失败用例；导航文档说明已实现入口、响应式/a11y与后续边界；截图与报告进入D05追踪，不声称性能或恢复已经验收。
- [ ] **11. 小提交。** `git add apps/web/src/features/access apps/web/src/features/history apps/web/src/features/system apps/web/src/app/router.tsx apps/web/src/api/generated.ts tests/e2e/workbench/access-history.spec.ts tests/e2e/workbench/accessibility.spec.ts docs/design/workbench-navigation.md docs/verification/workbench-api-coverage.md`。
- [ ] `git commit -m "feat(workbench): complete scoped access history and operational views"`。

## 交付门与自检

- [ ] R01–R04：U01/U02/U03/U05使用同一元数据模型、设备身份与三场景入口；D01负责三场景完整独立E2E。
- [ ] R08–R17/R33：U04配置与裁决/重算、U05解释/撤回界面对应I/S真实服务，不在浏览器产生权威事实。
- [ ] R20–R25/R34：U01真实会话，U06完整授权项/共享授权，U02/U04高风险审核，所有读取由后端约束。
- [ ] R26–R30：U04结构化/证据/语义文件入口，U05地图资源和离线资产，D02继续验证完整交付包冷启动。
- [ ] R35–R36：U03/U06统一历史与diff，展示旧修订真实内容，拒绝历史改写，不扩展成全平台历史回放。
- [ ] R31–R32：前端提供D04所需稳定定位器和真实流程，不设容量硬上限或未经批准的延迟SLA。
- [ ] 接口归属逐项检查：会话/授权/历史F03；任务健康F05；审批F06；Schema S02；对象几何S03；推理S04；查询S05；接入裁决重算I系列；地图资源U05。
- [ ] 重新生成并核对OpenAPI，删除重复DTO、硬编码样例成功及未定义前端调用；所有红绿命令执行结果附报告。
- [ ] 当前文件是实施计划，自检只能证明任务覆盖；实际浏览器、持久化、离线和性能检查尚未执行。
