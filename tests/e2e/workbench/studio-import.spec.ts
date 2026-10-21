import { test, expect as baseExpect } from "@playwright/test";
import { login } from "../support/workbench";
const expect = baseExpect.configure({ timeout: 30_000 });
test.use({ actionTimeout: 30_000 });
test("local source upload and import preserve original artifact through label edit and reload", async ({
  page,
}) => {
  test.setTimeout(120_000);
  await login(page);
  await page.goto("/ontology/studio");
  await page.getByRole("button", { name: "新建草稿", exact: true }).click();
  await page.getByText("导入本地 Schema / Shape 来源", { exact: true }).click();
  const source =
    '@prefix owl:<http://www.w3.org/2002/07/owl#>. @prefix rdfs:<http://www.w3.org/2000/01/rdf-schema#>. <urn:ImportedDevice> a owl:Class; rdfs:label "Imported device"@en; rdfs:comment "Original retained source"@en .';
  await page
    .getByLabel("本地语义文件")
    .setInputFiles({
      name: "owned-local-schema.ttl",
      mimeType: "text/turtle",
      buffer: Buffer.from(source),
    });
  await page.getByRole("button", { name: "保存来源制品" }).click();
  await page.getByRole("button", { name: "导入到当前草稿" }).click();
  await expect(page.getByLabel("中文名称", { exact: true })).toHaveValue(
    "Imported device",
  );
  await page.getByLabel("中文名称", { exact: true }).fill("保留来源的新名称");
  await page.getByRole("button", { name: "保存草稿", exact: true }).click();
  await expect(page.getByText("已保存到服务器", { exact: true })).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("中文名称", { exact: true })).toHaveValue(
    "保留来源的新名称",
  );
  const id = new URL(page.url()).searchParams.get("draft");
  const readback = await (
    await page.request.get(`/api/v1/schema/drafts/${id}`)
  ).json();
  expect(readback.document.dependencies).toHaveLength(1);
  const original = await page.request.get(
    `/api/v1/artifacts/${readback.document.dependencies[0].version_id}/content`,
  );
  expect(original.status()).toBe(200);
  expect(await original.text()).toBe(source);
});
