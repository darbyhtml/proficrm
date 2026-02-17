// Базовый конфиг Playwright для E2E-тестов Messenger.
// Тесты запускаются против уже запущенного backend'а.
// BASE_URL можно переопределить через переменную среды PLAYWRIGHT_BASE_URL.

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { defineConfig } = require('@playwright/test');

module.exports = defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  retries: process.env.CI ? 1 : 0,
  use: {
    baseURL: process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8000',
    headless: true,
    trace: 'on-first-retry',
  },
});

