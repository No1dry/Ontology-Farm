import { QueryClient } from '@tanstack/react-query';
import type { components } from './generated';
export type Session = components['schemas']['SessionDetail'];
export type SessionState = { status: 'anonymous' | 'checking' | 'ready' | 'suspended' | 'unavailable'; session: Session | null; error?: unknown };
export class SessionController {
  state: SessionState = { status: 'anonymous', session: null };
  epoch = 0;
  private generation = 0;
  private listeners = new Set<() => void>();
  private invalidators = new Set<() => void>();
  private reconciliation?: { epoch: number; promise: Promise<void> };
  private refreshing?: { epoch: number; promise: Promise<void> };
  private channel?: BroadcastChannel;
  private signedOut = false;
  constructor(public queries = new QueryClient(), public transport: typeof fetch = window.fetch.bind(window)) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.state;
  onInvalidate(listener: () => void) { this.invalidators.add(listener); return () => { this.invalidators.delete(listener); }; }
  private emit(state: SessionState) { this.state = state; this.listeners.forEach(fn => fn()); }
  private quarantine() {
    this.epoch++; void this.queries.cancelQueries(); this.queries.clear();
  }
  private clear() {
    this.quarantine();
    this.invalidators.forEach(fn => fn());
  }
  private assertEpoch(epoch: number) { if (epoch !== this.epoch) throw new SessionError('STALE_SESSION'); }
  private signal(signal: string) { this.channel?.postMessage({ signal, event: crypto.randomUUID() }); }
  private accept(session: Session) {
    this.signedOut = false;
    const previous = this.state.session;
    if (previous && (previous.user.id !== session.user.id || previous.authorization_version !== session.authorization_version)) {
      this.clear(); this.signal(previous.user.id !== session.user.id ? 'account-change' : 'authorization-change');
    }
    this.emit({ status: 'ready', session });
  }
  private async responseSession(response: Response): Promise<Session> {
    if (!response.ok) throw await responseError(response);
    return response.json() as Promise<Session>;
  }
  private sessionRequest(path: string, method = 'GET', csrf?: string) {
    return new Request(new URL(`/api/v1/session${path}`, window.location.origin), {
      method, credentials: 'include', cache: 'no-store', headers: csrf ? { 'X-CSRF-Token': csrf } : {},
    });
  }
  reconcile(): Promise<void> {
    const epoch = this.epoch;
    if (this.reconciliation?.epoch === epoch) return this.reconciliation.promise;
    this.emit({ ...this.state, status: 'checking' });
    let promise!: Promise<void>;
    promise = (async () => {
      try {
        const session = await this.responseSession(await this.transport(this.sessionRequest('')));
        this.assertEpoch(epoch); this.accept(session);
      } catch (error) {
        if (epoch !== this.epoch) return;
        if (error instanceof SessionError && error.status === 401) {
          this.clear(); this.emit({ status: 'anonymous', session: null, error });
        } else {
          // Keep only the prior identity baseline and quarantined retry intent.
          // Neither is usable until a current session is successfully reconciled.
          this.quarantine(); this.emit({ status: 'unavailable', session: this.state.session, error });
        }
      } finally { if (this.reconciliation?.promise === promise) this.reconciliation = undefined; }
    })();
    this.reconciliation = { epoch, promise }; return promise;
  }
  refresh(): Promise<void> {
    const epoch = this.epoch;
    if (this.refreshing?.epoch === epoch) return this.refreshing.promise;
    const session = this.state.session;
    if (!session) return Promise.reject(new SessionError('SESSION_REQUIRED'));
    let promise!: Promise<void>;
    promise = (async () => {
      try {
        const next = await this.responseSession(await this.transport(this.sessionRequest('/refresh', 'POST', session.csrf_token)));
        this.assertEpoch(epoch); this.generation++; this.accept(next);
      } catch (error) {
        if (epoch === this.epoch) { this.clear(); this.emit({ status: 'anonymous', session: null, error }); this.signal('logout'); }
        throw error;
      } finally { if (this.refreshing?.promise === promise) this.refreshing = undefined; }
    })();
    this.refreshing = { epoch, promise }; return promise;
  }
  async logout(): Promise<void> {
    const session = this.state.session;
    this.signedOut = true;
    this.clear(); this.emit({ status: 'anonymous', session: null }); this.signal('logout');
    if (!session) return;
    const epoch = this.epoch;
    try {
      const response = await this.transport(this.sessionRequest('/logout', 'POST', session.csrf_token));
      this.assertEpoch(epoch);
      if (!response.ok && response.status !== 401) throw await responseError(response);
      if (response.ok && !(await response.json()).provider_logged_out) {
        this.emit({ status: 'anonymous', session: null, error: new SessionError('PROVIDER_LOGOUT_INCOMPLETE') });
      }
    } catch (error) {
      if (epoch === this.epoch) this.emit({ status: 'unavailable', session: null, error: new SessionError('LOGOUT_UNCONFIRMED') });
    }
  }
  suspend(): void { this.quarantine(); this.emit({ ...this.state, status: 'suspended' }); }
  receive(signal: string): void {
    if (signal === 'logout') { this.signedOut = true; this.clear(); this.emit({ status: 'anonymous', session: null }); }
    else if (signal === 'account-change' || signal === 'authorization-change') { this.suspend(); if (document.visibilityState !== 'hidden') void this.reconcile(); }
  }
  connect(interval = 30_000) {
    this.channel = typeof BroadcastChannel === 'undefined' ? undefined : new BroadcastChannel('lzo-session');
    if (this.channel) this.channel.onmessage = event => this.receive(event.data?.signal);
    const resume = () => { if (document.visibilityState === 'hidden') { if (!this.signedOut) this.suspend(); } else if (!this.signedOut) void this.reconcile(); };
    window.addEventListener('focus', resume); document.addEventListener('visibilitychange', resume);
    const timer = window.setInterval(() => { if (document.visibilityState !== 'hidden' && this.state.session) void this.reconcile(); }, Math.max(1_000, interval));
    void this.reconcile();
    return () => { window.clearInterval(timer); window.removeEventListener('focus', resume); document.removeEventListener('visibilitychange', resume); this.channel?.close(); this.channel = undefined; this.suspend(); };
  }
  queryKey(feature: string, ...parts: unknown[]) {
    const session = this.state.session;
    if (this.state.status !== 'ready' || !session) throw new SessionError('SESSION_REQUIRED');
    return [session.user.id, session.authorization_version, feature, ...parts];
  }
  async fetch(request: Request): Promise<Response> {
    const session = this.state.session;
    if (!session || this.state.status !== 'ready') throw new SessionError('SESSION_REQUIRED');
    const url = new URL(request.url);
    if (url.origin !== window.location.origin || !url.pathname.startsWith('/api/v1/') || url.pathname.startsWith('/api/v1/session')) throw new SessionError('INVALID_API_PATH');
    const epoch = this.epoch, generation = this.generation;
    const headers = new Headers(request.headers);
    const safe = request.method === 'GET' || request.method === 'HEAD';
    if (!safe) headers.set('X-CSRF-Token', session.csrf_token);
    const outbound = new Request(request, { headers, credentials: 'include' });
    const replay = outbound.clone();
    let response = await this.transport(outbound);
    this.assertEpoch(epoch);
    if (response.status === 401) {
      if (generation === this.generation) await this.refresh();
      this.assertEpoch(epoch);
      if (safe) {
        response = await this.transport(replay); this.assertEpoch(epoch);
        if (response.status === 401) { this.clear(); this.emit({ status: 'anonymous', session: null }); this.signal('logout'); throw await responseError(response); }
      }
    } else if (response.status === 403 || response.status === 404) {
      // Reconcile current rights/CSRF, but do not repeat the denied operation.
      this.suspend(); await this.reconcile();
    }
    return response;
  }
}

export class SessionError extends Error {
  constructor(public code: string, public status = 0, public request_id?: string) { super(code); }
}
async function responseError(response: Response): Promise<SessionError> {
  let data: { code?: string; request_id?: string } = {};
  try { data = await response.json(); } catch { /* A proxy error need not be JSON. */ }
  return new SessionError(data.code || 'SERVICE_UNAVAILABLE', response.status, data.request_id || response.headers.get('X-Request-ID') || undefined);
}
export const sessionController = new SessionController(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } }));
