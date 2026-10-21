import { describe, expect, it } from 'vitest';
import { createApiClient } from './client';
import { QueryClient } from '@tanstack/react-query';
import { SessionController, type Session } from './session';

const session = (id = 'alice', version = 1): Session => ({ user: { id, issuer: 'https://identity.test', subject: id, display_name: id, email: null, enabled: true, revision: 1 }, permissions: [], authorization_version: version, csrf_token: 'test-csrf' });
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const unauthorized = () => json({ code: 'UNAUTHENTICATED', message: 'Login required', request_id: 'req-1', details: {} }, 401);
const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>(r => { resolve = r; }); return { promise, resolve }; };
const request = (method = 'GET') => new Request(new URL('/api/v1/users', window.location.origin), { method });
function harness(handler: (r: Request) => Promise<Response> | Response) {
  const queries = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const controller = new SessionController(queries, (async (r: RequestInfo | URL) => handler(r instanceof Request ? r : new Request(r))) as typeof fetch);
  return { controller, queries };
}

describe('BFF client transport', () => {
  it('does not dispatch a protected read before session reconciliation', async () => {
    const client = createApiClient();
    await expect(client.GET('/api/v1/users')).rejects.toMatchObject({ code: 'SESSION_REQUIRED' });
  });
});

describe('session lifecycle', () => {
  it('installs a real session snapshot and clears old user/version query data', async () => {
    let current = session();
    const { controller, queries } = harness(() => json(current));
    await controller.reconcile();
    expect(controller.state.session?.user.id).toBe('alice');
    queries.setQueryData(['alice', 1, 'private'], 'private-data');
    current = session('bob', 2);
    await controller.reconcile();
    expect(queries.getQueryData(['alice', 1, 'private'])).toBeUndefined();
    expect(controller.state.session?.user.id).toBe('bob');
  });

  it('fans simultaneous 401 reads into one refresh and retries each read only once', async () => {
    let reads = 0, refreshes = 0;
    const gate = deferred<Response>();
    const { controller } = harness(r => {
      if (r.url.endsWith('/session')) return json(session());
      if (r.url.endsWith('/refresh')) { refreshes++; return gate.promise; }
      reads++; return reads <= 2 ? unauthorized() : json({ items: [], next_cursor: null });
    });
    await controller.reconcile();
    const a = controller.fetch(request()), b = controller.fetch(request());
    await new Promise(r => setTimeout(r, 10));
    gate.resolve(json(session()));
    expect((await a).status).toBe(200); expect((await b).status).toBe(200);
    expect(refreshes).toBe(1); expect(reads).toBe(4);
  });

  it('does not refresh twice for a late 401 from the same request generation', async () => {
    const late = deferred<Response>(); let reads = 0, refreshes = 0;
    const { controller } = harness(r => {
      if (r.url.endsWith('/session')) return json(session());
      if (r.url.endsWith('/refresh')) { refreshes++; return json(session()); }
      reads++; if (reads === 1) return unauthorized(); if (reads === 2) return late.promise; return json({ items: [] });
    });
    await controller.reconcile();
    const a = controller.fetch(request()), b = controller.fetch(request());
    await a; late.resolve(unauthorized()); await b;
    expect(refreshes).toBe(1); expect(reads).toBe(4);
  });

  it('a rejected refresh clears cache and requires login', async () => {
    const { controller, queries } = harness(r => r.url.endsWith('/session') ? json(session()) : unauthorized());
    await controller.reconcile(); queries.setQueryData(['alice', 1, 'private'], 'secret');
    await expect(controller.fetch(request())).rejects.toBeDefined();
    expect(controller.state.status).toBe('anonymous'); expect(queries.getQueryCache().getAll()).toHaveLength(0);
  });

  it('never automatically replays a write after 401 and refresh', async () => {
    let writes = 0;
    const { controller } = harness(r => {
      if (r.url.endsWith('/session') || r.url.endsWith('/refresh')) return json(session());
      writes++; expect(r.headers.get('X-CSRF-Token')).toBe('test-csrf'); return unauthorized();
    });
    await controller.reconcile();
    expect((await controller.fetch(request('POST'))).status).toBe(401); expect(writes).toBe(1);
  });

  it('discards late reads after logout rather than leaking data into a new cache', async () => {
    const late = deferred<Response>();
    const { controller } = harness(r => r.url.endsWith('/session') ? json(session()) : r.url.endsWith('/logout') ? json({ logged_out: true, provider_logged_out: true }) : late.promise);
    await controller.reconcile(); const read = controller.fetch(request());
    await controller.logout(); late.resolve(json({ private: 'old-account' }));
    await expect(read).rejects.toMatchObject({ code: 'STALE_SESSION' });
    expect(controller.state.status).toBe('anonymous');
  });

  it('does not let a stale refresh restore a logged-out session', async () => {
    const late = deferred<Response>();
    const { controller } = harness(r => r.url.endsWith('/session') ? json(session()) : r.url.endsWith('/refresh') ? late.promise : json({ logged_out: true, provider_logged_out: true }));
    await controller.reconcile(); const refresh = controller.refresh();
    await controller.logout(); late.resolve(json(session()));
    await expect(refresh).rejects.toMatchObject({ code: 'STALE_SESSION' });
    expect(controller.state.session).toBeNull();
  });
  it('drops a delayed previous-account read after cookie identity reconciliation', async () => {
    const late = deferred<Response>(); let current = session();
    const { controller } = harness(r => r.url.endsWith('/session') ? json(current) : late.promise);
    await controller.reconcile(); const read = controller.fetch(request());
    current = session('bob', 1); await controller.reconcile();
    late.resolve(json({ private: 'alice-only' }));
    await expect(read).rejects.toMatchObject({ code: 'STALE_SESSION' });
    expect(controller.queryKey('objects')).toEqual(['bob', 1, 'objects']);
  });

  it('does not retry a safe read more than once if it remains unauthorized', async () => {
    let reads = 0, refreshes = 0;
    const { controller } = harness(r => {
      if (r.url.endsWith('/session')) return json(session());
      if (r.url.endsWith('/refresh')) { refreshes++; return json(session()); }
      reads++; return unauthorized();
    });
    await controller.reconcile(); await expect(controller.fetch(request())).rejects.toMatchObject({ status: 401 });
    expect(reads).toBe(2); expect(refreshes).toBe(1); expect(controller.state.session).toBeNull();
  });

  it.each(['AUTHORIZATION_CHANGED', 'FORBIDDEN', 'CSRF_INVALID'])('reconciles %s without provider refresh or write replay', async code => {
    let sessions = 0, refreshes = 0, writes = 0;
    const { controller } = harness(r => {
      if (r.url.endsWith('/session')) { sessions++; return json(session('alice', sessions)); }
      if (r.url.endsWith('/refresh')) { refreshes++; return json(session()); }
      writes++; return json({ code, message: 'denied', request_id: 'req-2', details: {} }, 403);
    });
    await controller.reconcile(); expect((await controller.fetch(request('POST'))).status).toBe(403);
    expect(sessions).toBe(2); expect(refreshes).toBe(0); expect(writes).toBe(1);
    expect(controller.state.session?.authorization_version).toBe(2);
  });

  it('suspends on hidden tab and refuses stale content when revalidation is unavailable', async () => {
    let offline = false;
    const { controller, queries } = harness(() => { if (offline) throw new TypeError('network'); return json(session()); });
    await controller.reconcile(); queries.setQueryData(['alice', 1, 'private'], 'secret');
    controller.suspend(); expect(controller.state.status).toBe('suspended');
    offline = true; await controller.reconcile();
    expect(controller.state.status).toBe('unavailable'); expect(queries.getQueryCache().getAll()).toHaveLength(0);
  });

  it('clears immediately on a sibling logout signal', async () => {
    const { controller, queries } = harness(() => json(session()));
    await controller.reconcile(); queries.setQueryData(['alice', 1, 'private'], 'secret');
    controller.receive('logout');
    expect(controller.state.status).toBe('anonymous'); expect(queries.getQueryCache().getAll()).toHaveLength(0);
  });
  it('does not restore a sibling logout on focus while the server logout is still in flight', async () => {
    const { controller } = harness(() => json(session()));
    const disconnect = controller.connect();
    await controller.reconcile(); controller.receive('logout');
    window.dispatchEvent(new Event('focus'));
    await new Promise(resolve => setTimeout(resolve, 5));
    try { expect(controller.state.status).toBe('anonymous'); expect(controller.state.session).toBeNull(); }
    finally { disconnect(); }
  });
  it('resumes focus checks after the user explicitly reconciles an unconfirmed logout', async () => {
    let current = session();
    const { controller } = harness(() => json(current));
    const disconnect = controller.connect(); await controller.reconcile();
    controller.receive('logout'); await controller.reconcile();
    current = session('alice', 2); window.dispatchEvent(new Event('focus'));
    await new Promise(resolve => setTimeout(resolve, 5));
    try { expect(controller.state.session?.authorization_version).toBe(2); }
    finally { disconnect(); }
  });
});
