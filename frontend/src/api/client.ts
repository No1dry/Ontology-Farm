import createClient, { type Client } from 'openapi-fetch';
import type { paths } from './generated';
import { sessionController, type SessionController } from './session';
export function createApiClient(session: SessionController = sessionController): Client<paths> {
  return createClient<paths>({ baseUrl: window.location.origin, credentials: 'include', fetch: request => session.fetch(request) });
}
