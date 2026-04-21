"""Pytest fixtures для user creation — shorthand для factories.

Usage (any pytest test):
    def test_something(user, authenticated_client):
        assert user.role == "manager"
        response = authenticated_client.get("/")
"""

from __future__ import annotations

import pytest

from tests.fixtures.factories import (
    AdminFactory,
    BranchDirectorFactory,
    BranchFactory,
    ManagerFactory,
    UserFactory,
)


@pytest.fixture
def user(db):
    """Default fixture: manager role, no branch attached."""
    return UserFactory()


@pytest.fixture
def admin_user(db):
    """Superuser + role=ADMIN."""
    return AdminFactory()


@pytest.fixture
def branch_ekb(db):
    """ЕКБ branch (staging uses code='ekb' — factory creates unique)."""
    return BranchFactory(code="ekb", name="Екатеринбург")


@pytest.fixture
def branch_tyumen(db):
    return BranchFactory(code="tyumen", name="Тюмень")


@pytest.fixture
def manager_ekb(db, branch_ekb):
    """Менеджер прикреплённый к ЕКБ branch."""
    return ManagerFactory(branch=branch_ekb)


@pytest.fixture
def branch_director(db, branch_ekb):
    return BranchDirectorFactory(branch=branch_ekb)


@pytest.fixture
def authenticated_client(client, user):
    """Django test client, logged in as `user` fixture."""
    client.force_login(user)
    return client


@pytest.fixture
def admin_client(client, admin_user):
    """Django test client, logged in as admin."""
    client.force_login(admin_user)
    return client
