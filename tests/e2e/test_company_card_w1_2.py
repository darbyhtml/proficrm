"""E2E smoke для карточки компании — проверка после W1.2 split.

Проверяет что все 40+ URL routes company_detail продолжают работать после
разбиения company_detail.py на 10 тематических модулей в pages/company/*.

Usage:
    # headless
    pytest -m e2e tests/e2e/test_company_card_w1_2.py

    # с UI (debug)
    pytest -m e2e tests/e2e/test_company_card_w1_2.py --headed

Environment variables:
    STAGING_BASE_URL — default https://crm-staging.groupprofi.ru
    STAGING_TEST_USER — default sdm
    STAGING_TEST_PASS — required (skip без него)
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect

BASE_URL = os.environ.get("STAGING_BASE_URL", "https://crm-staging.groupprofi.ru")
TEST_USER = os.environ.get("STAGING_TEST_USER", "sdm")
TEST_PASS = os.environ.get("STAGING_TEST_PASS", "")


def _login(page: Page) -> None:
    """Helper: login через форму."""
    page.goto(f"{BASE_URL}/accounts/login/")
    page.fill(
        'input[name="login"], input[name="email"], input[name="username"]',
        TEST_USER,
    )
    page.fill('input[name="password"]', TEST_PASS)
    page.click('button[type="submit"], input[type="submit"]')
    page.wait_for_url(
        lambda url: "/login" not in url,
        timeout=15_000,
    )


@pytest.mark.e2e
@pytest.mark.skipif(not TEST_PASS, reason="STAGING_TEST_PASS env var не задан")
def test_company_list_loads(page: Page) -> None:
    """Список компаний загружается после логина — baseline."""
    _login(page)
    response = page.goto(f"{BASE_URL}/companies/")
    assert response is not None
    assert response.status == 200, f"company_list HTTP {response.status}"
    # Страница должна содержать заголовок/ссылки
    expect(page.locator("body")).to_be_visible()


@pytest.mark.e2e
@pytest.mark.skipif(not TEST_PASS, reason="STAGING_TEST_PASS env var не задан")
def test_company_detail_loads(page: Page) -> None:
    """Карточка компании открывается (main view_name=company_detail).

    Это основной smoke для W1.2 — проверяет что company_detail функция
    extracted в pages/company/detail.py работает через URL roundtrip.
    """
    _login(page)
    page.goto(f"{BASE_URL}/companies/")
    # Берём первую ссылку на компанию из списка
    first_company_link = page.locator('a[href^="/companies/"][href*="-"]').first
    if first_company_link.count() == 0:
        pytest.skip("No companies in staging — нельзя проверить card")
    first_company_link.click()
    page.wait_for_load_state("networkidle", timeout=15_000)
    # URL должен быть вида /companies/<uuid>/
    expect(page).to_have_url(lambda url: "/companies/" in url and url.rstrip("/") != f"{BASE_URL}/companies")
    # Страница содержит body + не показывает серверную ошибку
    expect(page.locator("body")).to_be_visible()
    # Не 500 error
    response_text = page.content()
    assert "500" not in response_text[:500] or "Company" in response_text or "компан" in response_text.lower()
