import { SessionError, type SessionController } from '../api/session';
export function commandKey() { return crypto.randomUUID(); }
export class CommandIntent {
  private request: Request | null;
  private user: string;
  private unsubscribe: () => void;
  private pending = false;
  constructor(private controller: SessionController, request: Request) {
    if (controller.state.status !== 'ready' || !controller.state.session) throw new SessionError('SESSION_REQUIRED');
    if (['GET', 'HEAD', 'OPTIONS'].includes(request.method)) throw new Error('A command must be a mutation');
    this.user = controller.state.session.user.id;
    const headers = new Headers(request.headers); headers.set('Idempotency-Key', commandKey());
    this.request = new Request(request, { headers });
    this.unsubscribe = controller.onInvalidate(() => { this.request = null; this.unsubscribe(); });
  }
  async execute(): Promise<Response> {
    if (!this.request || this.controller.state.session?.user.id !== this.user) throw new SessionError('STALE_SESSION');
    if (this.pending) throw new Error('Command is already in progress');
    this.pending = true;
    try { return await this.controller.fetch(this.request.clone()); }
    finally { this.pending = false; }
  }
  dispose() { this.request = null; this.unsubscribe(); }
}
