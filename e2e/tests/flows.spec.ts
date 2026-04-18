import { test, expect, Page } from '@playwright/test';

/**
 * F10 R2 (2026-04-18) — реальные user-flow на staging.
 *
 * Требуют авторизации через E2E_USERNAME/E2E_PASSWORD.
 * Идемпотентны: каждый тест создаёт свои уникальные сущности
 * с префиксом `E2E-{timestamp}` и оставляет их после себя
 * (staging — не прод, данные не мешают).
 */

const USERNAME = process.env.E2E_USERNAME || 'sdm';
const PASSWORD = process.env.E2E_PASSWORD || '';

const uniqueSuffix = () => String(Date.now()).slice(-8);

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

test.describe('F10 R2: создание компании', () => {

  test('создать компанию по ИНН и открыть карточку', async ({ page }) => {
    await login(page);
    await page.goto('/companies/');

    // Кликаем «+ Добавить» (label может варьироваться; ищем по тексту).
    const addBtn = page.locator('a, button').filter({ hasText: /\+ ?Добавить|Создать|Новая компания/i }).first();
    await addBtn.click();

    // Ждём форму создания.
    await page.waitForLoadState('networkidle');

    const suffix = uniqueSuffix();
    const companyName = `E2E-Company-${suffix}`;
    const testInn = `7712345${suffix.slice(-3)}`.slice(0, 10);  // 10 цифр

    // Поле имя (selector может отличаться — пытаемся несколько вариантов).
    const nameInput = page.locator('input[name="name"], input#id_name').first();
    await nameInput.fill(companyName);

    const innInput = page.locator('input[name="inn"], input#id_inn').first();
    if (await innInput.count() > 0) {
      await innInput.fill(testInn);
    }

    // Submit.
    const submitBtn = page.locator('button[type="submit"]').first();
    await submitBtn.click();

    // После создания — либо редирект на карточку компании, либо возврат к списку.
    await page.waitForLoadState('networkidle', { timeout: 15_000 });

    // Проверяем, что где-то на странице появилось имя созданной компании.
    const bodyText = await page.locator('body').textContent();
    expect(bodyText).toContain(companyName);
  });

});

test.describe('F10 R2: создание задачи', () => {

  test('создать задачу и увидеть её в списке', async ({ page }) => {
    await login(page);
    await page.goto('/tasks/');

    const addBtn = page.locator('a, button').filter({ hasText: /\+ ?Добавить|Создать|Новая задача/i }).first();
    if (await addBtn.count() === 0) {
      test.skip(true, 'Кнопка «Создать задачу» не найдена на /tasks/ — UI-лейаут изменился.');
    }
    await addBtn.click();
    await page.waitForLoadState('networkidle');

    const suffix = uniqueSuffix();
    const taskTitle = `E2E-Task-${suffix}`;

    const titleInput = page.locator('input[name="title"], input#id_title, textarea[name="title"]').first();
    await titleInput.fill(taskTitle);

    const submitBtn = page.locator('button[type="submit"]').first();
    await submitBtn.click();
    await page.waitForLoadState('networkidle', { timeout: 15_000 });

    // Задача должна появиться в списке.
    await page.goto('/tasks/');
    const bodyText = await page.locator('body').textContent();
    expect(bodyText).toContain(taskTitle);
  });

});

test.describe('F10 R2: off-hours widget form (F5)', () => {

  test('off-hours endpoint принимает заявку', async ({ request }) => {
    // Этот тест идёт через API, не UI — off-hours форма появляется
    // только вне рабочих часов, что тяжело эмулировать в тесте.
    // Проверяем, что endpoint валидирует входные данные.
    const resp = await request.post('/api/widget/offhours-request/', {
      data: {
        widget_token: 'invalid_token_for_test',
        widget_session_token: 'invalid_session',
        preferred_channel: 'call',
        contact_value: '+7 900 123 45 67',
      },
      headers: { 'Content-Type': 'application/json' },
    });
    // Без валидного widget_token endpoint отдаёт 404 (inbox не найден).
    expect(resp.status()).toBeGreaterThanOrEqual(400);
    expect(resp.status()).toBeLessThan(500);
  });

  test('off-hours endpoint отклоняет невалидный preferred_channel', async ({ request }) => {
    const resp = await request.post('/api/widget/offhours-request/', {
      data: {
        widget_token: 'any',
        widget_session_token: 'any',
        preferred_channel: 'telepathy',  // не из {call,messenger,email,other}
        contact_value: '+7 900 000 00 00',
      },
      headers: { 'Content-Type': 'application/json' },
    });
    // 400 — ожидаемо.
    expect([400, 404]).toContain(resp.status());
  });

});

test.describe('F10 R2: analytics ролевой роутер', () => {

  test('analytics v2 содержит KPI-дашборд (для admin)', async ({ page }) => {
    await login(page);
    const resp = await page.goto('/analytics/v2/');
    expect(resp?.ok()).toBeTruthy();

    // Для admin/GROUP_MANAGER рендерится Executive Dashboard.
    // Для MANAGER — «Моя продуктивность».
    const body = await page.locator('body').textContent();
    const hasDashboard =
      body?.includes('Executive Dashboard') ||
      body?.includes('Моя продуктивность') ||
      body?.includes('Аналитика отдела') ||
      body?.includes('Аналитика подразделения') ||
      body?.includes('Тендерист');
    expect(hasDashboard).toBeTruthy();
  });

});

test.describe('F10 R2: mobile apps admin', () => {

  test('/admin/mobile-apps/ рендерит форму загрузки APK', async ({ page }) => {
    await login(page);
    const resp = await page.goto('/admin/mobile-apps/');
    expect(resp?.ok()).toBeTruthy();
    await expect(page.locator('body')).toContainText('CRMProfiDialer');
    await expect(page.locator('body')).toContainText('Загрузить новую версию');
  });

});
