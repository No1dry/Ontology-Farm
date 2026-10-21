import { test, expect } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';

test.use({ trace: 'off', screenshot: 'off', video: 'off' });

test('synthetic class approval: real independent accounts and revoked reviewer stop real worker', async ({ browser }) => {
  test.setTimeout(90_000);
  const baseURL = process.env.LZO_BASE_URL ?? 'https://localhost:18443';
  const contexts = await Promise.all([0, 1].map(() => browser.newContext({ baseURL, ignoreHTTPSErrors: true })));
  const helper = (...args: string[]) => JSON.parse(execFileSync(
    process.env.LZO_TEST_PYTHON ?? '.venv/Scripts/python.exe',
    ['tests/e2e/support/approval_fixture.py', ...args], { encoding: 'utf8' }));
  try {
    const sessions = [];
    for (const [index, name] of ['f03-synthetic', 'f03-admin'].entries()) {
      const page = await contexts[index].newPage();
      await page.goto('/api/v1/session/login');
      await page.locator('#username').fill(name);
      await page.locator('#password').fill('Synthetic-F03-only!');
      await page.locator('#kc-login').click();
      await page.waitForURL('**/api/v1/session');
      sessions.push(await (await page.request.get('/api/v1/session')).json());
      await page.goto('/health/live'); // failure context must not retain a session body
    }
    expect(sessions[0].user.id).not.toBe(sessions[1].user.id);
    const fixture = helper('setup', sessions[0].user.id, sessions[1].user.id);
    const headers = (index: number) => ({
      'X-CSRF-Token': sessions[index].csrf_token, 'Idempotency-Key': randomUUID(), 'If-Match': '"1"',
    });
    const preview = await contexts[0].request.post('/api/v1/approvals/preview', {
      headers: headers(0), data: { kind: 'schema.add_class', target_ids: [fixture.resource],
        command: { resource_id: fixture.resource, class_iri: 'urn:synthetic:f06:Class' } },
    });
    expect(preview.status()).toBe(200);
    const p = await preview.json();
    expect(p.risk).toBe('high');
    const submitted = await contexts[0].request.post('/api/v1/approvals', {
      headers: headers(0), data: { kind: p.kind, target_ids: p.target_ids, command: p.command,
        change_digest: p.change_digest, baseline: p.baseline },
    });
    expect(submitted.status()).toBe(201);
    const approval = await submitted.json();
    const url = `/api/v1/approvals/${approval.id}/decisions`;
    const self = await contexts[0].request.post(url, {
      headers: headers(0), data: { decision: 'approve', comment: 'Self-review must fail despite review permission' },
    });
    expect(self.status()).toBe(403);
    expect((await self.json()).code).toBe('SEPARATION_OF_DUTIES');
    const reviewed = await contexts[1].request.post(url, {
      headers: headers(1), data: { decision: 'approve', comment: 'Independent synthetic review' },
    });
    expect(reviewed.status()).toBe(200);
    expect((await reviewed.json()).reviewer_id).toBe(sessions[1].user.id);
    const positive = helper('enqueue', approval.id);
    await expect.poll(() => helper('evidence', positive.job_id).status, { timeout: 20_000 }).toBe('succeeded');
    expect(helper('evidence', positive.job_id).effects).toBe(1);
    const nextPreview = await contexts[0].request.post('/api/v1/approvals/preview', {
      headers: headers(0), data: { kind: p.kind, target_ids: p.target_ids, command: p.command },
    });
    expect(nextPreview.status()).toBe(200);
    const next = await nextPreview.json();
    const nextSubmitted = await contexts[0].request.post('/api/v1/approvals', {
      headers: headers(0), data: { kind: next.kind, target_ids: next.target_ids, command: next.command,
        change_digest: next.change_digest, baseline: next.baseline },
    });
    expect(nextSubmitted.status()).toBe(201);
    const nextApproval = await nextSubmitted.json();
    const nextReviewed = await contexts[1].request.post(`/api/v1/approvals/${nextApproval.id}/decisions`, {
      headers: headers(1), data: { decision: 'approve', comment: 'Independent synthetic review of current version' },
    });
    expect(nextReviewed.status()).toBe(200);
    const job = helper('revoke-enqueue', nextApproval.id);
    await expect.poll(() => helper('evidence', job.job_id).status, { timeout: 20_000 }).toBe('failed');
    const evidence = helper('evidence', job.job_id);
    expect(evidence.effects).toBe(0);
    expect(evidence.error_code).toBe('HANDLER_FAILED');
  } finally {
    await Promise.all(contexts.map(context => context.close()));
  }
});
