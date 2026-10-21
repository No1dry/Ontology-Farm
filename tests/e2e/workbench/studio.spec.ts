import { test, expect as baseExpect } from "@playwright/test";
import { login } from "../support/workbench";
const expect = baseExpect.configure({ timeout: 30_000 });
test.use({ actionTimeout: 30_000 });
test("new device type is a governed schema change", async ({
  page,
  browser,
}) => {
  test.setTimeout(240_000);
  await login(page, "editor-a");
  await page.goto("/ontology/studio");
  await expect(
    page.getByRole("button", { name: "新建草稿", exact: true }),
  ).toBeVisible({ timeout: 10_000 });
  await expect(page.getByLabel("新草稿基于").locator("option")).not.toHaveCount(
    1,
  );
  await page.getByLabel("新草稿基于").selectOption({ index: 1 });
  await page.getByRole("button", { name: "新建草稿", exact: true }).click();
  await page.getByRole("button", { name: "新建类", exact: true }).click();
  await page.getByLabel("中文名称", { exact: true }).fill("多场景氨氮传感器");
  console.log(
    "Studio local class created; parent list count",
    await page.getByRole("listbox", { name: "父类", exact: true }).count(),
  );
  await page
    .getByRole("listbox", { name: "父类", exact: true })
    .selectOption({ label: "Sensor" });
  const anonymous = await page
    .getByLabel("Shape 主体")
    .locator("option")
    .evaluateAll((options) =>
      options
        .map((o) => (o as HTMLOptionElement).value)
        .find((v) => v.startsWith("_:")),
    );
  expect(anonymous).toBeTruthy();
  await page.getByLabel("Shape 主体").selectOption(anonymous!);
  await page
    .getByRole("combobox", { name: "约束", exact: true })
    .selectOption("maxCount");
  await page.getByLabel("约束值", { exact: true }).fill("2");
  await page.getByRole("button", { name: "应用约束", exact: true }).click();
  await expect(
    page.getByRole("status").filter({ hasText: "规范化序列化" }),
  ).toBeVisible();
  await page.getByText("迁移计划与 UI 元数据", { exact: true }).click();
  const metadata = JSON.parse(
    await page.getByLabel("UI 元数据", { exact: true }).inputValue(),
  );
  metadata.migration_plan = {
    description: "备注最大基数放宽至 2",
    validation: "验证现有对象满足新约束",
    rollback: "恢复原最大基数 1",
  };
  await page
    .getByLabel("UI 元数据", { exact: true })
    .fill(JSON.stringify(metadata));
  await page
    .getByRole("button", { name: "应用UI 元数据", exact: true })
    .click();
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("已保存到服务器", { exact: true })).toBeVisible();
  await expect(
    page.getByText("来源：urn:Sensor", { exact: true }),
  ).toBeVisible();
  console.log("Studio saved with inherited field");
  await page
    .getByRole("button", { name: "校验并预览影响", exact: true })
    .click();
  await expect(
    page.getByText("高风险 · 需要其他人员审核", { exact: true }),
  ).toBeVisible();
  await page.getByRole("button", { name: "提交审核", exact: true }).click();
  await expect(page.getByRole("link", { name: "查看审核" })).toBeVisible();
  console.log("Studio submitted actual approval");
  const reviewURL = await page
    .getByRole("link", { name: "查看审核" })
    .getAttribute("href");
  const reviewer = await browser.newContext({ ignoreHTTPSErrors: true });
  try {
    const second = await reviewer.newPage();
    await login(second, "editor-b");
    await second.goto(reviewURL!);
    await second
      .getByLabel("审核意见")
      .fill("已检查完整文档与基线，批准合成测试结构。");
    await second.getByRole("button", { name: "审核通过", exact: true }).click();
    await expect(second.getByText("已批准", { exact: true })).toBeVisible();
    await second
      .getByRole("heading", { name: "治理与审查", exact: true })
      .scrollIntoViewIfNeeded();
    await second.screenshot({ path: ".runtime/u02/studio-review-1280.png" });
    console.log("Independent reviewer approved");
    await page.bringToFront();
    await page.getByRole("button", { name: "刷新审核状态" }).click();
    await page
      .getByRole("button", { name: "发布已批准版本", exact: true })
      .click();
    await expect(page.getByText("发布完成", { exact: true })).toBeVisible({
      timeout: 90_000,
    });
    await expect(page.getByText("精确发布版本")).toBeVisible();
    await page
      .getByRole("heading", { name: "发布完成", exact: true })
      .scrollIntoViewIfNeeded();
    await page.screenshot({
      path: ".runtime/u02/studio-published-1280.png",
    });
  } finally {
    await reviewer.close();
  }
});
