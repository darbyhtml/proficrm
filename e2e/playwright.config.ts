import { defineConfig, devices } from '@playwright/test';

/**
 * F10 (2026-04-18) — Playwright config для E2E smoke-тестов CRM ПРОФИ.
 *
 * Запуск:
 *   npm run install:deps   # установка playwright + chromium
 *   npm run test:staging   # против crm-staging.groupprofi.ru
 *   npm run test           # против BASE_URL (по умолчанию staging)
 *
 * Прод НЕ тестируем из E2E — это правило проекта.
 */
export default defineConfig({
  testDir: './tests',
  timeout: 30_000,
  expect: { timeout: 5_000 },
  fullyParallel: false,  // Django session — один user, последовательно.
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  workers: 1,
  reporter: [
    ['html', { open: 'never' }],
    ['list'],
  ],
  use: {
    baseURL: process.env.BASE_URL || 'https://crm-staging.groupprofi.ru',
    ignoreHTTPSErrors: false,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    video: 'retain-on-failure',
    locale: 'ru-RU',
    timezoneId: 'Asia/Yekaterinburg',
    viewport: { width: 1440, height: 900 },
  },
  projects: [
    {
      name: 'chromium-desktop',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
});
