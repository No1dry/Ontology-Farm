import { test, expect } from '@playwright/test';

test('same-origin HTTPS exposes the real platform and OIDC discovery', async ({ request }) => {
  const live = await request.get('/health/live');
  expect(live.ok()).toBeTruthy();
  expect(await live.json()).toEqual({ status: 'live' });
  const discovery = await request.get('/identity/realms/lzo/.well-known/openid-configuration');
  expect(discovery.ok()).toBeTruthy();
  const document = await discovery.json();
  const origin = process.env.LZO_PUBLIC_ORIGIN ?? 'https://localhost:18443';
  expect(document.issuer).toBe(`${origin}/identity/realms/lzo`);
  expect(document.authorization_endpoint).toBe(`${origin}/identity/realms/lzo/protocol/openid-connect/auth`);
  expect(document.response_types_supported).toContain('code');
});
