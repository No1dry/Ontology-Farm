import { test, expect as baseExpect, type Page } from "@playwright/test";
import { login, createNamedContext } from "../support/workbench";
const expect = baseExpect.configure({ timeout: 30_000 });
test.use({ actionTimeout: 30_000 });

async function newBasedDraft(page: Page) {
  await page.goto("/ontology/studio");
  const option = page
    .getByLabel("新草稿基于")
    .locator("option")
    .filter({ hasText: /· published$/ });
  await expect(option).toHaveCount(1);
  const active = await option.getAttribute("value");
  expect(active).toBeTruthy();
  await page.getByLabel("新草稿基于").selectOption(active!);
  await page.getByRole("button", { name: "新建草稿", exact: true }).click();
  await expect(page.getByLabel("中文名称", { exact: true })).toBeVisible();
  return new URL(page.url()).searchParams.get("draft")!;
}
async function readDraft(page: Page, id: string) {
  const r = await page.request.get(`/api/v1/schema/drafts/${id}`);
  expect(r.status()).toBe(200);
  return r.json();
}
async function write(
  page: Page,
  path: string,
  method: string,
  body: unknown,
  revision: number,
) {
  const session = await (await page.request.get("/api/v1/session")).json();
  return page.request.fetch(path, {
    method,
    data: body,
    headers: {
      Origin: new URL(page.url()).origin,
      "X-CSRF-Token": session.csrf_token,
      "If-Match": `"${revision}"`,
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
}
test("conflict requires explicit merge and offline retry preserves exact intent and source", async ({
  page,
  browser,
}) => {
  test.setTimeout(150_000);
  await login(page);
  const id = await newBasedDraft(page);
  const original = await readDraft(page, id);
  await page.getByLabel("中文名称", { exact: true }).fill("本地未提交名称");
  const other = await createNamedContext(browser, "editor-b");
  try {
    const remote = structuredClone(original.document);
    remote.ui_metadata.remote_note = "concurrent audit";
    const changed = await write(
      other.page,
      `/api/v1/schema/drafts/${id}`,
      "PATCH",
      { document: remote },
      original.revision,
    );
    expect(changed.status()).toBe(200);
    await page.bringToFront();
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "REVISION_CONFLICT" }),
    ).toBeVisible();
    await expect(page.getByLabel("中文名称", { exact: true })).toHaveValue(
      "本地未提交名称",
    );
    await page
      .getByRole("button", { name: "读取最新版本", exact: true })
      .click();
    await page.getByRole("button", { name: "显式合并无冲突区段" }).click();
    const writes: {
      key: string | null;
      body: string | null;
      revision: string | null;
    }[] = [];
    page.on("request", (request) => {
      if (
        request.method() === "PATCH" &&
        request.url().endsWith("/schema/drafts/" + id)
      )
        writes.push({
          key: request.headers()["idempotency-key"] || null,
          body: request.postData(),
          revision: request.headers()["if-match"] || null,
        });
    });
    await page.context().setOffline(true);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page.getByText("本地修改尚未确认保存", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "重试原保存命令（相同请求与幂等键）" }),
    ).toBeVisible();
    await page.context().setOffline(false);
    await page
      .getByRole("button", { name: "重试原保存命令（相同请求与幂等键）" })
      .click();
    await expect(
      page.getByText("已保存到服务器", { exact: true }),
    ).toBeVisible();
    expect(writes).toHaveLength(2);
    expect(writes[0]).toEqual(writes[1]);
    expect(writes[0].key).toBeTruthy();
    const after = await readDraft(page, id);
    expect(after.document.shapes).toBe(original.document.shapes);
    expect(after.document.dependencies).toEqual(original.document.dependencies);
    expect(after.document.ui_metadata.fields).toEqual(
      original.document.ui_metadata.fields,
    );
    expect(after.document.ui_metadata.remote_note).toBe("concurrent audit");
    await page.reload();
    await expect(page.getByLabel("中文名称", { exact: true })).toHaveValue(
      "本地未提交名称",
    );
    await page.getByRole("button", { name: "打开 Schema 画布" }).click();
    await expect(page.getByText("Schema 结构", { exact: true })).toBeVisible();
    await page
      .getByRole("heading", { name: "本体工作室", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({ path: ".runtime/u02/studio-editor-1280.png" });
    await page.setViewportSize({ width: 768, height: 900 });
    await page.screenshot({ path: ".runtime/u02/studio-editor-768.png" });
  } finally {
    await page.context().setOffline(false);
    await other.context.close();
  }
});

test("stale approvals and unsupported or cyclic definitions retain unsent content", async ({
  page,
  browser,
}) => {
  test.setTimeout(180_000);
  await login(page);
  const id = await newBasedDraft(page);
  await page.getByLabel("中文名称", { exact: true }).fill("待审名称");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("已保存到服务器", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "校验并预览影响" }).click();
  await page.getByRole("button", { name: "提交审核", exact: true }).click();
  const link = page.getByRole("link", { name: "查看审核" });
  await expect(link).toBeVisible();
  const reviewURL = await link.getAttribute("href");
  const approvalId = new URL(reviewURL!, page.url()).searchParams.get(
    "approval",
  )!;
  const approval = await (
    await page.request.get("/api/v1/approvals/" + approvalId)
  ).json();
  const own = await write(
    page,
    `/api/v1/approvals/${approvalId}/decisions`,
    "POST",
    { decision: "approve", comment: "Self review must fail" },
    approval.revision,
  );
  expect(own.status()).toBe(403);
  const other = await createNamedContext(browser, "editor-b");
  try {
    await other.page.goto(reviewURL!);
    await other.page.getByLabel("审核意见").fill("完整核对后批准");
    await other.page
      .getByRole("button", { name: "审核通过", exact: true })
      .click();
    await expect(other.page.getByText("已批准", { exact: true })).toBeVisible();
    await page.bringToFront();
    await page.getByLabel("中文名称", { exact: true }).fill("批准后变更");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page.getByText("已保存到服务器", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("alert").filter({ hasText: "APPROVAL_STALE" }),
    ).toBeVisible();
    await expect(
      page.getByRole("button", { name: "发布已批准版本" }),
    ).toBeDisabled();
    await other.page.reload();
    await expect(
      other.page.getByRole("alert").filter({ hasText: "APPROVAL_STALE" }),
    ).toBeVisible();
    await expect(
      other.page.getByRole("button", { name: "审核通过" }),
    ).toBeDisabled();
    await page.bringToFront();
    const original = await page.getByLabel("Shape 原始源码").inputValue();
    const unsupported =
      original +
      '\n<urn:Unsupported> <http://www.w3.org/ns/shacl#sparql> [ <http://www.w3.org/ns/shacl#select> "SELECT ?this WHERE { ?this ?p ?o }" ] .';
    await page.getByLabel("Shape 原始源码").fill(unsupported);
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "SEMANTIC_PROFILE_UNSUPPORTED" }),
    ).toBeVisible();
    await expect(page.getByLabel("Shape 原始源码")).toHaveValue(unsupported);
    await page.getByLabel("Shape 原始源码").fill(original);
    await page
      .getByRole("listbox", { name: "父类", exact: true })
      .selectOption("urn:Sensor");
    await page.getByRole("button", { name: "保存草稿", exact: true }).click();
    await expect(
      page.getByRole("alert").filter({ hasText: "INHERITANCE_CYCLE" }),
    ).toBeVisible();
    expect(
      (await readDraft(page, id)).document.classes[0].parents,
    ).not.toContain("urn:Sensor");
  } finally {
    await other.context.close();
  }
});

test("inherited required-field violations and missing migration plan block real submission", async ({
  page,
}) => {
  test.setTimeout(150_000);
  await login(page);
  const id = await newBasedDraft(page);
  const initial = await readDraft(page, id);
  const metadata = { ...initial.document.ui_metadata };
  delete metadata.migration_plan;
  await page.getByText("迁移计划与 UI 元数据", { exact: true }).click();
  await page
    .getByLabel("UI 元数据", { exact: true })
    .fill(JSON.stringify(metadata));
  await page
    .getByRole("button", { name: "应用UI 元数据", exact: true })
    .click();
  await page.getByText("迁移计划与 UI 元数据", { exact: true }).click();
  await page.getByRole("button", { name: "新建类", exact: true }).click();
  await page.getByLabel("中文名称", { exact: true }).fill("必填继承测试");
  await page
    .getByRole("listbox", { name: "父类", exact: true })
    .selectOption("urn:Sensor");
  await page.getByLabel("约束目标类 IRI").fill("urn:Sensor");
  await page.getByLabel("约束属性 IRI").fill("urn:note");
  await page.getByRole("button", { name: "添加属性 Shape" }).click();
  await page.getByRole("button", { name: "应用约束", exact: true }).click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("已保存到服务器", { exact: true })).toBeVisible();
  await expect(page.getByText("Note · 必填", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "校验并预览影响" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "MIGRATION_PLAN_REQUIRED" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "提交审核", exact: true }),
  ).toBeDisabled();
  const saved = await readDraft(page, id);
  const validation = await write(
    page,
    `/api/v1/schema/drafts/${id}/validate`,
    "POST",
    undefined,
    saved.revision,
  );
  expect(validation.status()).toBe(200);
  const impact = await validation.json();
  expect(impact.violations.length).toBeGreaterThan(0);
  const refused = await write(
    page,
    `/api/v1/schema/drafts/${id}/submit`,
    "POST",
    { impact_digest: impact.digest },
    saved.revision,
  );
  expect(refused.status()).toBe(422);
  expect((await refused.json()).code).toBe("MIGRATION_PLAN_REQUIRED");
  await page.getByText("迁移计划与 UI 元数据", { exact: true }).click();
  await page.getByLabel("UI 元数据", { exact: true }).fill(
    JSON.stringify({
      ...initial.document.ui_metadata,
      migration_plan: {
        description: "补齐备注",
        validation: "全部 Sensor 必填",
        rollback: "恢复旧约束",
      },
    }),
  );
  await page
    .getByRole("button", { name: "应用UI 元数据", exact: true })
    .click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("已保存到服务器", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "校验并预览影响" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "MIGRATION_NOT_APPLIED" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "提交审核", exact: true }),
  ).toBeDisabled();
  const final = await readDraft(page, id);
  const preview = await write(
    page,
    `/api/v1/schema/drafts/${id}/validate`,
    "POST",
    undefined,
    final.revision,
  );
  const now = await preview.json();
  const blocked = await write(
    page,
    `/api/v1/schema/drafts/${id}/submit`,
    "POST",
    { impact_digest: now.digest },
    final.revision,
  );
  expect(blocked.status()).toBe(422);
  expect((await blocked.json()).code).toBe("MIGRATION_NOT_APPLIED");
});
