// E2E: полный сценарий "виджет → сообщение → ответ в панели".
//
// Требования:
// - запущен backend (например, `python backend/manage.py runserver 0.0.0.0:8000`);
// - создан тестовый Inbox с включённым Messenger и доступной демо-страницей `/widget-demo/?token=...`;
// - заданы переменные окружения:
//   - PLAYWRIGHT_BASE_URL          — базовый URL, по умолчанию http://localhost:8000;
//   - MESSENGER_TEST_WIDGET_TOKEN  — widget_token для демо-виджета;
//   - MESSENGER_ADMIN_EMAIL        — логин администратора CRM;
//   - MESSENGER_ADMIN_PASSWORD     — пароль администратора CRM.
//
// Запуск:
//   npx playwright test e2e/messenger-widget.spec.js

// eslint-disable-next-line @typescript-eslint/no-var-requires
const { test, expect } = require('@playwright/test');

const BASE_URL = process.env.PLAYWRIGHT_BASE_URL || 'http://localhost:8000';
const WIDGET_TOKEN = process.env.MESSENGER_TEST_WIDGET_TOKEN;
const ADMIN_EMAIL = process.env.MESSENGER_ADMIN_EMAIL;
const ADMIN_PASSWORD = process.env.MESSENGER_ADMIN_PASSWORD;

test.describe('Messenger E2E: виджет → сообщение → ответ в панели', () => {
  test.skip(!WIDGET_TOKEN, 'MESSENGER_TEST_WIDGET_TOKEN не задан — пропускаем E2E для виджета');
  test.skip(!ADMIN_EMAIL || !ADMIN_PASSWORD, 'Учётные данные администратора не заданы — пропускаем E2E для панели оператора');

  test('полный сценарий: виджет → сообщение → ответ оператора возвращается в виджет', async ({ page, browser }) => {
    const uniqueSuffix = Date.now();
    const visitorMessage = `E2E widget message ${uniqueSuffix}`;
    const operatorReply = `E2E operator reply ${uniqueSuffix}`;

    // 1. Открываем демо-страницу виджета.
    const widgetUrl = `${BASE_URL}/widget-demo/?token=${encodeURIComponent(WIDGET_TOKEN)}`;
    await page.goto(widgetUrl);

    // 2. Открываем кнопку виджета и отправляем сообщение.
    const button = page.locator('#messenger-widget-container .messenger-widget-button');
    await button.click();

    const input = page.locator('#messenger-widget-container .messenger-widget-input');
    await expect(input).toBeVisible();

    await input.fill(visitorMessage);
    await page.keyboard.press('Enter');

    // Сообщение посетителя должно появиться в ленте виджета.
    const visitorMsgLocator = page.locator(
      '#messenger-widget-container .messenger-widget-message-body',
      { hasText: visitorMessage }
    );
    await expect(visitorMsgLocator).toBeVisible();

    // 3. В новой вкладке/контексте заходим в панель оператора под админом.
    const context = await browser.newContext();
    const operatorPage = await context.newPage();

    await operatorPage.goto(`${BASE_URL}/login/`);

    // Логин в CRM (селекторы могут отличаться, при необходимости подправить под конкретный проект).
    await operatorPage.fill('input[name="email"], input[name="username"]', ADMIN_EMAIL);
    await operatorPage.fill('input[name="password"]', ADMIN_PASSWORD);
    await operatorPage.click('button[type="submit"]');

    // Переходим в список диалогов.
    await operatorPage.goto(`${BASE_URL}/messenger/conversations/`);

    // Ищем диалог по тексту нашего сообщения.
    const conversationRow = operatorPage.locator('text=' + visitorMessage).first();
    await expect(conversationRow).toBeVisible();
    await conversationRow.click();

    // В деталях диалога отправляем ответ.
    const replyTextarea = operatorPage.locator('textarea[name="body"], textarea.messenger-reply-input');
    await expect(replyTextarea).toBeVisible();
    await replyTextarea.fill(operatorReply);

    const sendButton = operatorPage.locator('button[type="submit"], button.messenger-reply-send').first();
    await expect(sendButton).toBeVisible();
    await sendButton.click();

    // 4. Возвращаемся в виджет и ждём появления ответа оператора (через SSE/poll).
    const operatorMsgLocator = page.locator(
      '#messenger-widget-container .messenger-widget-message-body',
      { hasText: operatorReply }
    );
    await expect(operatorMsgLocator).toBeVisible({ timeout: 15_000 });
  });
});

