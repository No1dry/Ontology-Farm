import { test, expect } from '@playwright/test';
import { login, logout } from '../support/workbench';
import { execFileSync } from 'node:child_process';
function control(command: string, alias = 'editor-a') {
  if (!process.env.LZO_FIXTURE_CONTROL || !process.env.LZO_FIXTURE_PYTHON) throw new Error('Owned APP_ENV=test fixture controls required');
  return execFileSync(process.env.LZO_FIXTURE_PYTHON, ['-B', process.env.LZO_FIXTURE_CONTROL, command, alias], { windowsHide: true, timeout: 20_000, encoding: 'utf8' }).trim();
}

test.use({ trace: 'off', screenshot: 'off', video: 'off' });
test.afterEach(async ({ context }) => {
  // Playwright's automatic error context can otherwise snapshot callback JSON.
  await Promise.all(context.pages().map(page => page.goto('about:blank').catch(() => {})));
});
test('real OIDC session opens Chinese workbench with protected cookie and no browser tokens', async ({ page, context }) => {
  await login(page);
  expect(await page.evaluate(() => [localStorage.getItem('access_token'), localStorage.getItem('refresh_token'), sessionStorage.getItem('access_token'), sessionStorage.getItem('refresh_token')])).toEqual([null, null, null, null]);
  const cookie = (await context.cookies()).find(item => item.name === '__Host-lzo_session');
  expect(Boolean(cookie?.httpOnly && cookie.secure && cookie.sameSite === 'Lax' && cookie.path === '/')).toBe(true);
  const deadline = control('deadlines');
  const status = await page.evaluate(async () => {
    const snapshot = await (await fetch('/api/v1/session')).json();
    const missing = await fetch('/api/v1/session/refresh', { method: 'POST', credentials: 'include' });
    const refreshed = await fetch('/api/v1/session/refresh', { method: 'POST', credentials: 'include', headers: { 'X-CSRF-Token': snapshot.csrf_token } });
    return [missing.status, refreshed.status];
  });
  expect(status).toEqual([403, 200]);
  expect(control('deadlines')).toBe(deadline);
  expect((await context.cookies()).find(item => item.name === '__Host-lzo_session')?.expires).toBe(cookie?.expires);
  await logout(page);
});

test('two same-origin tabs clear protected content after logout', async ({ page, context }) => {
  await login(page);
  const second = await context.newPage(); await second.goto('/overview');
  await expect(second.getByRole('heading', { name: '当前访问' })).toBeVisible();
  await page.bringToFront(); await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  await logout(page);
  await second.bringToFront();
  await expect(second.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  await expect(second.getByRole('heading', { name: '当前访问' })).toHaveCount(0);
});

test('invalid and consumed callback state offers recovery while API callers retain JSON', async ({ page }) => {
  await page.goto('/api/v1/session/login'); await expect(page.locator('#username')).toBeVisible();
  const state = new URL(page.url()).searchParams.get('state');
  await page.goto('/api/v1/session/callback?state=invalid-owned-test&code=invalid-owned-test');
  await expect(page.getByRole('alert')).toContainText('登录链接已失效');
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  const response = await page.request.get('/api/v1/session/callback?state=invalid-owned-test&code=invalid-owned-test', { headers: { Accept: 'application/json' }, maxRedirects: 0 });
  expect(response.status()).toBe(401);
  expect((await response.json()).code).toBe('OIDC_STATE_INVALID');
  await page.evaluate(state => { window.location.assign('/api/v1/session/callback?' + new URLSearchParams({ state: state!, code: 'owned-consumed-state-test' })); }, state);
  await expect(page.getByRole('alert')).toContainText('登录链接已失效');
  await page.goto('/auth/error?code=not-allowed&request_id=%3Cscript%3E');
  await expect(page.getByRole('alert')).toContainText('操作未完成');
  await expect(page.getByRole('alert')).not.toContainText('<script>');
});

test('real signed provider nonce mismatch fails closed with recoverable UI', async ({ page }) => {
  await page.goto('/api/v1/session/login'); await expect(page.locator('#username')).toBeVisible();
  control('nonce');
  await page.locator('#username').fill('editor-a'); await page.locator('#password').fill(process.env.LZO_E2E_PASSWORD!); await page.locator('#kc-login').click();
  await expect(page.getByRole('alert')).toContainText('身份验证未通过');
  expect((await page.request.get('/api/v1/session')).status()).toBe(401);
});

test('hard BFF expiry requires login and does not revive through refresh', async ({ page }) => {
  await login(page);
  control('expire');
  const refresh = await page.evaluate(async () => {
    // CSRF is deliberately absent: an expired session must fail authentication first.
    return (await fetch('/api/v1/session/refresh', { method: 'POST' })).status;
  });
  expect(refresh).toBe(401);
  await page.getByRole('button', { name: /重新核验权限/ }).click();
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
  await expect(page.getByRole('heading', { name: '当前访问' })).toHaveCount(0);
});

test('real provider refresh rejection invalidates the BFF session', async ({ page }) => {
  await login(page); control('provider-logout');
  const status = await page.evaluate(async () => {
    const snapshot = await (await fetch('/api/v1/session')).json();
    return (await fetch('/api/v1/session/refresh', { method: 'POST', headers: { 'X-CSRF-Token': snapshot.csrf_token } })).status;
  });
  expect(status).toBe(401);
  await page.getByRole('button', { name: /重新核验权限/ }).click();
  await expect(page.getByRole('button', { name: '登录', exact: true })).toBeVisible();
});

test('observed authorization change updates two tabs from their own current sessions', async ({ page, context }) => {
  await login(page);
  const before = await page.locator('dd').nth(1).textContent();
  const second = await context.newPage(); await second.goto('/overview');
  await expect(second.getByRole('heading', { name: '当前访问' })).toBeVisible();
  control('version');
  await page.getByRole('button', { name: /重新核验权限/ }).click();
  await expect(page.locator('dd').nth(1)).not.toHaveText(before!);
  await expect(second.locator('dd').nth(1)).not.toHaveText(before!);
  await logout(second);
});

test('revoked rights disappear after real reconciliation and the backend refuses the old operation', async ({ page }) => {
  await login(page, 'editor-b');
  expect((await page.request.get('/api/v1/users')).status()).toBe(200);
  try {
    control('revoke', 'editor-b');
    await page.getByRole('button', { name: /重新核验权限/ }).click();
    await expect(page.locator('dd').nth(2)).toHaveText('0 项');
    expect((await page.request.get('/api/v1/users')).status()).toBe(403);
    await logout(page);
  } finally { control('restore', 'editor-b'); }
});

test('same-session polling preserves collapsed navigation and confirmed rights changes replace it', async ({ page }) => {
  await page.clock.install();
  await login(page);
  await page.getByRole('button', { name: '折叠导航' }).click();
  const polled = page.waitForResponse(response => new URL(response.url()).pathname === '/api/v1/session' && response.request().method() === 'GET');
  await page.clock.fastForward(30_000); await polled;
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible();
  await page.getByRole('button', { name: /重新核验权限/ }).click();
  await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible();
  control('version');
  await page.getByRole('button', { name: /重新核验权限/ }).click();
  await expect(page.getByRole('button', { name: '折叠导航' })).toBeVisible();
  await logout(page);
});

test('keyboard, local font and 768/1280 layouts keep delivered controls reachable', async ({ page }) => {
  const external: string[] = [];
  await login(page);
  const applicationOrigin = new URL(page.url()).origin;
  page.on('request', request => { const url = new URL(request.url()); if (url.origin !== applicationOrigin) external.push(url.origin); });
  await page.reload(); await expect(page.getByRole('navigation', { name: '主导航' })).toBeVisible();
  for (const width of [768, 1280]) {
    await page.setViewportSize({ width, height: 900 });
    await page.getByRole('link', { name: '跳到主要内容' }).focus(); await page.keyboard.press('Enter');
    await expect(page.getByRole('main')).toBeFocused();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
    await page.getByRole('button', { name: '折叠导航' }).click(); await expect(page.getByRole('button', { name: '展开导航' })).toBeVisible();
    await page.getByRole('button', { name: '展开导航' }).click();
    await page.getByRole('button', { name: '退出登录', exact: true }).focus(); await expect(page.getByRole('button', { name: '退出登录', exact: true })).toBeFocused();
    await page.screenshot({ path: `.runtime/u01/workbench-${width}.png`, fullPage: true });
  }
  expect(await page.evaluate(async () => { await document.fonts.load('14px NotoFarm'); return document.fonts.check('14px NotoFarm', '本体农场'); })).toBe(true);
  expect(external).toEqual([]);
  await page.goto('/graph'); await expect(page.getByRole('heading', { name: '知识图谱 · 待实现' })).toBeVisible();
});
