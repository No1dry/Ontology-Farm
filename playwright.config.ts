import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: './tests/e2e',
  timeout: 30_000,
  fullyParallel: false,
  use: {
    baseURL: process.env.LZO_BASE_URL ?? 'https://localhost:18443',
    ignoreHTTPSErrors: true, // Development certificate only; never installed into global trust.
    trace: 'off', // Auth flows must never record provider codes, cookies or token responses.
    launchOptions: { executablePath: process.env.LZO_BROWSER_EXECUTABLE },
  },
  reporter: [['list'], ['html', { open: 'never' }]],
  projects: [
    { name: 'api-identity', testIgnore: '**/workbench/**', use: { browserName: 'chromium', trace: 'off', screenshot: 'off', video: 'off' } },
    { name: 'workbench', testMatch: '**/workbench/**/*.spec.ts', use: { browserName: 'chromium', trace: 'off', screenshot: 'off', video: 'off' } },
  ],
});
