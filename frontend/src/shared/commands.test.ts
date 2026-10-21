import { expect, it } from 'vitest';
import { CommandIntent } from './commands';
import { SessionController, type Session } from '../api/session';
import { QueryClient } from '@tanstack/react-query';
const session: Session = { user: { id: 'alice', issuer: 'test', subject: 'alice', display_name: 'Alice', email: null, enabled: true, revision: 1 }, permissions: [], authorization_version: 1, csrf_token: 'test-csrf' };
it('an explicit same-user unknown-outcome retry preserves the exact idempotent intent', async () => {
  const received: string[] = []; let writes = 0;
  const controller = new SessionController(new QueryClient(), (async (input: Request) => {
    if (input.url.endsWith('/session')) return Response.json(session);
    received.push(JSON.stringify([input.method, input.url, await input.text(), input.headers.get('If-Match'), input.headers.get('Idempotency-Key')]));
    if (++writes === 1) throw new TypeError('lost response after server commit');
    return Response.json({ id: 'existing-result' });
  }) as typeof fetch);
  await controller.reconcile();
  const intent = new CommandIntent(controller, new Request(new URL('/api/v1/groups', window.location.origin), { method: 'POST', body: JSON.stringify({ name: 'same-intent' }), headers: { 'Content-Type': 'application/json', 'If-Match': '"2"' } }));
  await expect(intent.execute()).rejects.toBeDefined();
  expect(writes).toBe(1);
  expect((await intent.execute()).status).toBe(200); expect(received[0]).toBe(received[1]);
  controller.receive('logout');
  await expect(intent.execute()).rejects.toMatchObject({ code: 'STALE_SESSION' });
  expect(writes).toBe(2);
});

it.each(['hidden', 'transport failure'])('quarantines an unknown-outcome command through %s and retries the exact intent after same-user reconciliation', async interruption => {
  const received: string[] = []; let offline = false;
  const controller = new SessionController(new QueryClient(), (async (input: Request) => {
    if (input.url.endsWith('/session')) { if (offline) throw new TypeError('offline'); return Response.json(session); }
    received.push(JSON.stringify([input.method, input.url, await input.text(), input.headers.get('If-Match'), input.headers.get('Idempotency-Key')]));
    if (received.length === 1) throw new TypeError('lost response after commit');
    return Response.json({ id: 'committed-result' });
  }) as typeof fetch);
  await controller.reconcile();
  const intent = new CommandIntent(controller, new Request(new URL('/api/v1/groups', window.location.origin), { method: 'PATCH', body: '{"name":"original"}', headers: { 'If-Match': '"7"' } }));
  await expect(intent.execute()).rejects.toBeInstanceOf(TypeError);
  controller.suspend();
  if (interruption === 'transport failure') { offline = true; await controller.reconcile(); }
  await expect(intent.execute()).rejects.toMatchObject({ code: 'SESSION_REQUIRED' });
  expect(received).toHaveLength(1);
  offline = false; await controller.reconcile();
  expect(received).toHaveLength(1);
  expect((await intent.execute()).status).toBe(200);
  expect(received[1]).toBe(received[0]); intent.dispose();
});

it.each(['user', 'authorization'])('permanently discards quarantined retries after confirmed %s change', async change => {
  let current = session, writes = 0;
  const controller = new SessionController(new QueryClient(), (async (input: Request) => {
    if (input.url.endsWith('/session')) return Response.json(current);
    writes++; throw new TypeError('lost response');
  }) as typeof fetch);
  await controller.reconcile();
  const intent = new CommandIntent(controller, new Request(new URL('/api/v1/groups', window.location.origin), { method: 'POST', body: '{}' }));
  await expect(intent.execute()).rejects.toBeInstanceOf(TypeError);
  controller.suspend();
  current = change === 'user' ? { ...session, user: { ...session.user, id: 'bob' } } : { ...session, authorization_version: 2 };
  await controller.reconcile();
  await expect(intent.execute()).rejects.toMatchObject({ code: 'STALE_SESSION' });
  current = session; await controller.reconcile();
  await expect(intent.execute()).rejects.toMatchObject({ code: 'STALE_SESSION' });
  expect(writes).toBe(1);
});
