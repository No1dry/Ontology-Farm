import { test, expect as baseExpect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import { login } from "../support/workbench";
const expect = baseExpect.configure({ timeout: 30_000 });
test("revocation removes loaded Studio content and refuses job, result and source reads", async ({
  page,
}) => {
  test.setTimeout(120_000);
  if (
    !process.env.LZO_STUDIO_MANIFEST ||
    !process.env.LZO_FIXTURE_CONTROL ||
    !process.env.LZO_FIXTURE_PYTHON
  )
    throw new Error("Owned Studio fixture required");
  const fixture = JSON.parse(
    readFileSync(process.env.LZO_STUDIO_MANIFEST, "utf8"),
  );
  await login(page, "editor-b");
  const job = await page.request.get(`/api/v1/jobs/${fixture.publication_job_id}`);
  expect(job.status()).toBe(200);
  const result = (await job.json()).result_ref;
  expect(result.kind).toBe("schema.release");
  const release = await page.request.get(result.api_path);
  expect(release.status()).toBe(200);
  const dependency = (await release.json()).document.dependencies[0];
  const contentPath = `/api/v1/artifacts/${dependency.version_id}/content`;
  expect((await page.request.get(contentPath)).status()).toBe(200);
  await page.goto(`/ontology/studio?draft=${fixture.draft_id}`);
  await expect(page.getByLabel("中文名称", { exact: true })).toBeVisible();
  await page.getByLabel("中文名称", { exact: true }).fill("撤权前本地内容");
  execFileSync(
    process.env.LZO_FIXTURE_PYTHON,
    ["-B", process.env.LZO_FIXTURE_CONTROL, "revoke", "editor-b"],
    { windowsHide: true, timeout: 20_000, encoding: "utf8" },
  );
  await page.evaluate(() => window.dispatchEvent(new Event("focus")));
  await expect(page.getByLabel("中文名称", { exact: true })).toHaveCount(0);
  for (const path of [
    `/api/v1/jobs/${fixture.publication_job_id}`,
    result.api_path,
    contentPath,
  ]) {
    const response = await page.request.get(path);
    expect([403, 404]).toContain(response.status());
  }
  await expect(page.getByRole("alert")).toBeVisible();
  await page.screenshot({
    path: ".runtime/u02/studio-permission-loss-1280.png",
  });
  // This is intentionally the last fixture test: revoked synthetic grants remain revoked.
});
