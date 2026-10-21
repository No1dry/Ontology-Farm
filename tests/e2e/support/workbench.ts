import { expect, type Page, type Browser } from "@playwright/test";
import type { components } from "../../../frontend/src/api/generated";

export async function login(page: Page, alias = "editor-a") {
  const password = process.env.LZO_E2E_PASSWORD;
  if (!password)
    throw new Error("Owned workbench fixture password is required");
  await page.goto("/api/v1/session/login");
  await expect(page.locator("#username")).toBeVisible();
  await page.locator("#username").fill(alias);
  await page.locator("#password").fill(password);
  await page.locator("#kc-login").click();
  // Do not log callback URLs, session responses or provider input on failure.
  await expect(page.getByRole("navigation", { name: "主导航" })).toBeVisible();
}
export async function logout(page: Page) {
  await page.getByRole("button", { name: "退出登录", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "登录", exact: true }),
  ).toBeVisible();
}
export async function createNamedContext(browser: Browser, alias: string) {
  const context = await browser.newContext({ ignoreHTTPSErrors: true });
  const page = await context.newPage();
  try {
    await login(page, alias);
    return { context, page };
  } catch (error) {
    await context.close();
    throw error;
  }
}
export async function waitJob(
  page: Page,
  id: string,
  timeout = 90_000,
): Promise<components["schemas"]["JobView"]> {
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    const response = await page.request.get(`/api/v1/jobs/${id}`);
    if (!response.ok())
      throw new Error(`Job ${id} read failed (${response.status()})`);
    const view = (await response.json()) as components["schemas"]["JobView"];
    if (view.job.status === "succeeded") return view;
    if (["failed", "paused", "cancelled"].includes(view.job.status))
      throw new Error(
        `Job ${id}: ${view.job.status} (${view.error_code || "NO_CODE"})`,
      );
    await page.waitForTimeout(500);
  }
  throw new Error(`Job ${id} timed out after ${timeout}ms`);
}
