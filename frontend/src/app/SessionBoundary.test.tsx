import { afterEach, beforeEach, expect, it } from 'vitest';
import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { SessionBoundary } from './SessionBoundary';
import { sessionController as controller, type Session } from '../api/session';

const session: Session = { user: { id: 'alice', issuer: 'test', subject: 'alice', display_name: 'Alice', email: null, enabled: true, revision: 1 }, permissions: [], authorization_version: 1, csrf_token: 'synthetic-csrf' };
const originalTransport = controller.transport;
beforeEach(() => {
  window.history.replaceState(null, '', '/overview');
  controller.receive('logout');
  controller.transport = async () => Response.json(session);
});
afterEach(() => { cleanup(); controller.receive('logout'); controller.transport = originalTransport; });

async function collapsedShell() {
  render(<SessionBoundary />);
  fireEvent.click(await screen.findByRole('button', { name: '折叠导航' }));
  return screen.getByRole('navigation', { name: '主导航' });
}
function pendingCheck() {
  let resolve!: (response: Response) => void;
  controller.transport = () => new Promise<Response>(done => { resolve = done; });
  let checked!: Promise<void>;
  act(() => { checked = controller.reconcile(); });
  return async (response: Response) => { await act(async () => { resolve(response); await checked; }); };
}

it('preserves the mounted collapsed shell across routine checks and temporary hiding, without exposing unvalidated controls', async () => {
  const navigation = await collapsedShell();
  const finish = pendingCheck();
  expect(navigation.isConnected).toBe(true);
  expect(screen.queryByRole('navigation', { name: '主导航' })).toBeNull();
  expect(navigation.closest('[inert]')).not.toBeNull();
  await finish(Response.json(session));
  expect(screen.getByRole('navigation', { name: '主导航' })).toBe(navigation);
  expect(screen.getByRole('button', { name: '展开导航' })).toBeVisible();
  act(() => controller.suspend());
  expect(navigation.isConnected).toBe(true);
  expect(screen.queryByRole('button', { name: '退出登录' })).toBeNull();
  controller.transport = async () => Response.json(session);
  await act(async () => { await controller.reconcile(); });
  expect(screen.getByRole('button', { name: '展开导航' })).toBeVisible();
});

it('quarantines the mounted tree during unavailable checks so same-session recovery keeps local state', async () => {
  const navigation = await collapsedShell();
  const finish = pendingCheck();
  await finish(Response.json({ code: 'SERVICE_UNAVAILABLE' }, { status: 503 }));
  expect(navigation.isConnected).toBe(true);
  expect(screen.queryByRole('navigation', { name: '主导航' })).toBeNull();
  expect(navigation.closest('[inert]')).not.toBeNull();
  expect(screen.getByRole('button', { name: '重新核验' })).toBeVisible();
  controller.transport = async () => Response.json(session);
  await act(async () => { await controller.reconcile(); });
  expect(screen.getByRole('button', { name: '展开导航' })).toBeVisible();
  expect(screen.getByRole('navigation', { name: '主导航' })).toBe(navigation);
});

it('removes protected local state when reconciliation confirms authentication failure', async () => {
  const navigation = await collapsedShell();
  const finish = pendingCheck();
  await finish(Response.json({ code: 'UNAUTHENTICATED' }, { status: 401 }));
  expect(navigation.isConnected).toBe(false);
  expect(screen.queryByRole('navigation', { name: '主导航' })).toBeNull();
  expect(screen.getByRole('button', { name: '登录' })).toBeVisible();
});

it.each(['user', 'authorization'])('replaces the protected tree when the confirmed %s changes', async change => {
  const navigation = await collapsedShell();
  const finish = pendingCheck();
  const next = change === 'user' ? { ...session, user: { ...session.user, id: 'bob', display_name: 'Bob' } } : { ...session, authorization_version: 2 };
  await finish(Response.json(next));
  expect(navigation.isConnected).toBe(false);
  expect(screen.getByRole('button', { name: '折叠导航' })).toBeVisible();
});
