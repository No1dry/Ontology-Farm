import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

test.use({ trace: 'off', screenshot: 'off', video: 'off' });
test.afterEach(async ({ context }) => { await Promise.all(context.pages().map(page => page.goto('about:blank').catch(() => {}))); });

test('real authorization-code form login, CSRF, refresh and logout', async ({ page, context }) => {
  await page.goto('/api/v1/session/login');
  await expect(page.locator('#username')).toBeVisible();
  await page.locator('#username').fill(process.env.LZO_E2E_USERNAME ?? 'f03-synthetic');
  await page.locator('#password').fill(process.env.LZO_E2E_PASSWORD ?? 'Synthetic-F03-only!');
  await page.locator('#kc-login').click();
  await page.waitForURL('**/overview');
  const session = await page.request.get('/api/v1/session');
  expect(session.status()).toBe(200);
  const body = await session.json();
  expect(body.user.id).toBeTruthy();
  expect(body.csrf_token).toBeTruthy();
  expect(body).not.toHaveProperty('access_token');
  expect(body).not.toHaveProperty('refresh_token');
  expect(await page.evaluate(() => Object.keys(localStorage))).toEqual([]);
  const cookie = (await context.cookies()).find(c => c.name === '__Host-lzo_session');
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.secure).toBe(true);
  expect(cookie?.sameSite).toBe('Lax');
  expect((await page.request.post('/api/v1/session/refresh')).status()).toBe(403);
  const headers = { 'X-CSRF-Token': body.csrf_token };
  const refreshed = await Promise.all([
    page.request.post('/api/v1/session/refresh', { headers }),
    page.request.post('/api/v1/session/refresh', { headers }),
  ]);
  expect(refreshed.map(r => r.status())).toEqual([200, 200]);
  const loggedOut = await page.request.post('/api/v1/session/logout', { headers });
  expect(loggedOut.status()).toBe(200);
  expect(await loggedOut.json()).toEqual({ logged_out: true, provider_logged_out: true });
  expect((await page.request.get('/api/v1/session')).status()).toBe(401);
  await page.goto('/api/v1/session/login');
  await expect(page.locator('#username')).toBeVisible();
});

test('real SMTP invitation activation, one-time action and user disabling', async ({ page, browser }) => {
  test.setTimeout(90_000);
  await page.goto('/api/v1/session/login');
  await page.locator('#username').fill('f03-admin');
  await page.locator('#password').fill('Synthetic-F03-only!');
  await page.locator('#kc-login').click();
  await page.waitForURL('**/overview');
  const adminSession = await (await page.request.get('/api/v1/session')).json();
  const email = `f03-${randomUUID()}@example.invalid`;
  const invited = await page.request.post('/api/v1/users/invitations', {
    headers: { 'X-CSRF-Token': adminSession.csrf_token, 'Idempotency-Key': randomUUID() },
    data: { email, display_name: 'Synthetic Invite' },
  });
  expect(invited.status()).toBe(201);
  const invitation = await invited.json();
  expect(invitation.phase).toBe('completed');
  const user = invitation.user;
  const python = process.env.LZO_TEST_PYTHON ?? '.venv/Scripts/python.exe';
  const link = execFileSync(python, ['tests/e2e/support/mail_link.py', email], { encoding: 'utf8' }).trim();
  const invitedContext = await browser.newContext({ ignoreHTTPSErrors: true, baseURL: new URL(page.url()).origin });
  const invitedPage = await invitedContext.newPage();
  try {
    try { await invitedPage.goto(link); } catch { throw new Error('Invitation page navigation failed (URL redacted)'); }
    const proceed = invitedPage.getByRole('link', { name: /proceed/i });
    if (await proceed.isVisible()) await proceed.click();
    await invitedPage.locator('#password-new').fill('Synthetic-Activation-only!');
    await invitedPage.locator('#password-confirm').fill('Synthetic-Activation-only!');
    await invitedPage.getByRole('button', { name: /submit/i }).click();
    if (await invitedPage.locator('input[name="lastName"]').isVisible()) {
      await invitedPage.locator('input[name="firstName"]').fill('Synthetic');
      await invitedPage.locator('input[name="lastName"]').fill('Invite');
      await invitedPage.getByRole('button', { name: /submit/i }).click();
    }
    // The action may finish on an informational page before returning to login.
    await invitedPage.goto('/api/v1/session/login');
    if (await invitedPage.locator('#username').isVisible()) {
      await invitedPage.locator('#username').fill(email);
      await invitedPage.locator('#password').fill('Synthetic-Activation-only!');
      await invitedPage.locator('#kc-login').click();
    }
    await invitedPage.waitForURL('**/overview');
    expect((await invitedPage.request.get('/api/v1/session')).status()).toBe(200);
    const replay = await invitedPage.request.get(link);
    expect(await replay.text()).toMatch(/no longer valid|expired|already.*used|invalid/i);
    const disabled = await page.request.patch('/api/v1/users/' + user.id, {
      headers: { 'X-CSRF-Token': adminSession.csrf_token, 'Idempotency-Key': randomUUID(), 'If-Match': `"${user.revision}"` },
      data: { enabled: false },
    });
    expect(disabled.status()).toBe(200);
    expect((await disabled.json()).phase).toBe('completed');
    expect((await invitedPage.request.get('/api/v1/session')).status()).toBe(401);
  } finally { await invitedPage.goto('about:blank').catch(() => {}); await invitedContext.close(); }
});

test('callback rejects unbound state', async ({ request }) => {
  const response = await request.get('/api/v1/session/callback?code=synthetic-invalid&state=unbound');
  expect(response.status()).toBe(401);
  expect((await response.json()).code).toBe('OIDC_STATE_INVALID');
});
