"""Hot-path factories для test fixtures (Wave 0.5).

Только модели с 50+ использованиями в существующих тестах:
- User / Branch — identity fixtures.
- Company — самый referenced.
- Contact — companion to Company.
- Task — часто нужен для task-lifecycle tests.

Остальные 65 models — в W1 test infrastructure follow-up.

Usage:
    from tests.fixtures.factories import UserFactory, CompanyFactory

    def test_something(db):
        user = UserFactory()
        company = CompanyFactory(created_by=user)
"""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory
from faker import Faker

fake = Faker("ru_RU")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

class BranchFactory(DjangoModelFactory):
    """Branch — 3 реальных на проде (ЕКБ, Тюмень, Краснодар).

    Для тестов: unique code через Sequence (ekb1, ekb2, ...).
    """

    class Meta:
        model = "accounts.Branch"
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"br{n}")
    name = factory.Sequence(lambda n: f"Подразделение #{n}")
    is_active = True


class UserFactory(DjangoModelFactory):
    """User — identity fixture.

    Defaults: MANAGER role, no branch (test must attach via factory arg).
    Roles available (see accounts.models.User.Role): MANAGER, BRANCH_DIRECTOR,
    SALES_HEAD, GROUP_MANAGER, TENDERIST, ADMIN.
    """

    class Meta:
        model = "accounts.User"
        django_get_or_create = ("username",)
        skip_postgeneration_save = True

    username = factory.Sequence(lambda n: f"testuser{n}")
    email = factory.LazyAttribute(lambda obj: f"{obj.username}@test.groupprofi.local")
    first_name = factory.LazyFunction(lambda: fake.first_name())
    last_name = factory.LazyFunction(lambda: fake.last_name())
    role = "manager"  # Role.MANAGER
    is_active = True

    @classmethod
    def _create(cls, model_class, *args, **kwargs):
        """Use create_user() to properly hash password."""
        password = kwargs.pop("password", "testpass123")
        manager = model_class.objects
        # create_user require username + password; email via kwargs.
        user = manager.create_user(
            username=kwargs.pop("username"),
            password=password,
            **kwargs,
        )
        return user


class AdminFactory(UserFactory):
    """Superuser + role=ADMIN."""

    role = "admin"
    is_staff = True
    is_superuser = True


class ManagerFactory(UserFactory):
    """Default MANAGER role. Test attaches branch via factory arg if needed."""

    role = "manager"


class BranchDirectorFactory(UserFactory):
    role = "branch_director"


# ---------------------------------------------------------------------------
# Companies
# ---------------------------------------------------------------------------

class CompanyFactory(DjangoModelFactory):
    """Company — основная сущность CRM.

    Defaults: случайный название + ИНН (10-значный, валидный формат).
    Для связанных полей (branch, created_by) — передавай в factory args.
    """

    class Meta:
        model = "companies.Company"

    name = factory.LazyFunction(lambda: fake.company())
    # ИНН 10-значный (юрлица) — random digits, корректный формат
    inn = factory.Sequence(lambda n: f"{7700000000 + n:010d}")
    activity_kind = factory.LazyFunction(lambda: fake.bs())
    address = factory.LazyFunction(lambda: fake.address().replace("\n", ", "))


class ContactFactory(DjangoModelFactory):
    """Contact — прикреплён к Company."""

    class Meta:
        model = "companies.Contact"

    company = factory.SubFactory(CompanyFactory)
    name = factory.LazyFunction(lambda: fake.name())
    position = factory.LazyFunction(lambda: fake.job())


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------

class TaskFactory(DjangoModelFactory):
    """Task — операционные задачи по компаниям."""

    class Meta:
        model = "tasksapp.Task"

    title = factory.LazyFunction(lambda: fake.sentence(nb_words=4).rstrip("."))
    description = factory.LazyFunction(lambda: fake.text(max_nb_chars=200))
