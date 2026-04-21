"""Waffle feature flag fixtures — short-hand для override_flag context.

Covers 4 W0.3 seed flags + MESSENGER_ENABLED.
"""

from __future__ import annotations

import pytest
from waffle.testutils import override_flag


@pytest.fixture
def ui_v3b_off():
    """Default state — legacy UI (or would-be-legacy)."""
    with override_flag("UI_V3B_DEFAULT", active=False):
        yield


@pytest.fixture
def ui_v3b_on():
    """Activate v3b редизайн для tests that verify new UI."""
    with override_flag("UI_V3B_DEFAULT", active=True):
        yield


@pytest.fixture
def messenger_enabled():
    """Enables messenger endpoints (off by default per settings.py).

    Required for any test что touches /api/conversations/... endpoints.
    See backend/messenger/api.py::MessengerEnabledApiMixin.
    """
    with override_flag("MESSENGER_ENABLED", active=True):
        yield


@pytest.fixture
def policy_engine_enforce():
    """Activate POLICY_ENGINE_ENFORCE — from observe → enforce transition (W2)."""
    with override_flag("POLICY_ENGINE_ENFORCE", active=True):
        yield


@pytest.fixture
def two_factor_mandatory():
    """2FA mandatory mode (W2)."""
    with override_flag("TWO_FACTOR_MANDATORY_FOR_ADMINS", active=True):
        yield
