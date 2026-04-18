import { test, expect, Page } from '@playwright/test';

/**
 * F10 (2026-04-18) — Smoke-тесты критических UI flow.
 *
 * Credentials берутся из env:
 *   E2E_USERNAME, E2E_PASSWORD
 *
 * Для staging — используйте учётку sdm (есть в reference_staging_sdm_credentials).
 */

const USERNAME = process.env.E2E_USERNAME || 'sdm';
const PASSWORD = process.env.E2E_PASSWORD || '';

async function login(page: Page) {
  if (!PASSWORD) {
    test.skip(true, 'E2E_PASSWORD не задан — пропускаем авто-логин.');
  }
  await page.goto('/login/');
  await page.locator('input[name="username"]').fill(USERNAME);
  await page.locator('input[name="password"]').fill(PASSWORD);
  await page.locator('button[type="submit"]').click();
  await page.waitForURL(url => !url.pathname.startsWith('/login'), { timeout: 10_000 });
}

test.describe('F10 smoke: базовые страницы', () => {

  test('login → dashboard открывается', async ({ page }) => {
    await login(page);
    await page.goto('/');
    await expect(page).toHaveTitle(/CRM|ПРОФИ/i);
  });

  test('companies list отдаёт 200', async ({ page }) => {
    await login(page);
    const response = await page.goto('/companies/');
    expect(response?.ok()).toBeTruthy();
  });

  test('tasks list отдаёт 200', async ({ page }) => {
    await login(page);
    const response = await page.goto('/tasks/');
    expect(response?.ok()).toBeTruthy();
  });

  test('analytics v2 (роль-роутер) отвечает 200 для admin', async ({ page }) => {
    await login(page);
    const response = await page.goto('/analytics/v2/');
    expect(response?.ok()).toBeTruthy();
    // Админ попадает в group_manager dashboard
    await expect(page.locator('body')).toContainText(/(Executive Dashboard|Моя продуктивность|Аналитика)/);
  });

  test('settings: вкладка «Отсутствие» доступна', async ({ page }) => {
    await login(page);
    await page.goto('/settings/');
    // Sidebar должен содержать «Отсутствие» — F5 UserAbsence.
    await expect(page.locator('body')).toContainText('Отсутствие');
  });

  test('help: FAQ рендерится', async ({ page }) => {
    await login(page);
    await page.goto('/help/');
    await expect(page.locator('body')).toContainText('Быстрый старт');
    await expect(page.locator('body')).toContainText('Частые вопросы');
  });

  test('admin/mail/setup: форма SMTP доступна (F6 R2)', async ({ page }) => {
    await login(page);
    const response = await page.goto('/admin/mail/setup/');
    expect(response?.ok()).toBeTruthy();
    await expect(page.locator('body')).toContainText('SMTP сервер и отправитель');
  });

});

test.describe('F10 smoke: health endpoint (без auth)', () => {
  test('/health/ отдаёт 200', async ({ page }) => {
    const response = await page.goto('/health/');
    expect(response?.status()).toBe(200);
  });
});
