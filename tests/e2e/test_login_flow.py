"""E2E login flow против staging (Wave 0.5 example test).

Usage:
    # single run
    pytest -m e2e tests/e2e/test_login_flow.py --headed

    # headless (CI-friendly)
    pytest -m e2e tests/e2e/test_login_flow.py

Environment variables:
    STAGING_BASE_URL — default https://crm-staging.groupprofi.ru
    STAGING_TEST_USER — username (default sdm)
    STAGING_TEST_PASS — required for real auth, NOT committed

НЕ в main CI — запускается через отдельный workflow `.github/workflows/e2e.yml`
(TODO в W1) или manually. Slow (~2-5 сек), требует browser binary
(`playwright install chromium`).
"""

from __future__ import annotations

import os

import pytest
from playwright.sync_api import Page, expect


BASE_URL = os.environ.get("STAGING_BASE_URL", "https://crm-staging.groupprofi.ru")
TEST_USER = os.environ.get("STAGING_TEST_USER", "sdm")
TEST_PASS = os.environ.get("STAGING_TEST_PASS", "")


@pytest.mark.e2e
@pytest.mark.skipif(not TEST_PASS, reason="STAGING_TEST_PASS env var не задан")
def test_login_flow_staging(page: Page) -> None:
    """Smoke: login through real browser against staging."""
    page.goto(f"{BASE_URL}/accounts/login/")

    # Login form — username + password
    page.fill(
        'input[name="login"], input[name="email"], input[name="username"]',
        TEST_USER,
    )
    page.fill('input[name="password"]', TEST_PASS)

    # Submit
    page.click('button[type="submit"], input[type="submit"]')

    # Wait for redirect off /login/
    page.wait_for_url(
        lambda url: "/login" not in url,
        timeout=15_000,
    )

    # Basic post-login expectation: logged in, NOT redirected back to login.
    expect(page).not_to_have_url(f"{BASE_URL}/accounts/login/")


@pytest.mark.e2e
def test_homepage_loads_without_auth(page: Page) -> None:
    """Anonymous visit to / redirects to login or renders public page.

    Не требует credentials (поэтому не skipif-gated). Smoke для basic routing.
    """
    response = page.goto(f"{BASE_URL}/")

    # Любой ответ OK: 200 (public home) или 302 → /accounts/login/ (protected).
    assert response is not None
    assert response.status < 500, f"Server error on homepage: {response.status}"
