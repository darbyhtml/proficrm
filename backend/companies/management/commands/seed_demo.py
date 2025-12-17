from django.core.management.base import BaseCommand

from accounts.models import Branch, User
from companies.models import Company


class Command(BaseCommand):
    help = "Создать демо-данные: филиалы, demo admin/manager и одну тестовую компанию."

    def handle(self, *args, **options):
        ekb, _ = Branch.objects.get_or_create(code="ekb", defaults={"name": "Екатеринбург"})
        krd, _ = Branch.objects.get_or_create(code="krd", defaults={"name": "Краснодар"})
        tmn, _ = Branch.objects.get_or_create(code="tmn", defaults={"name": "Тюмень"})

        admin, admin_created = User.objects.get_or_create(
            username="admin",
            defaults={
                "email": "admin@example.com",
                "role": User.Role.ADMIN,
                "data_scope": User.DataScope.GLOBAL,
                "is_staff": True,
                "is_active": True,
                "first_name": "Админ",
                "last_name": "CRM",
            },
        )
        if admin_created:
            admin.set_password("admin12345")
            admin.save()

        manager, manager_created = User.objects.get_or_create(
            username="manager1",
            defaults={
                "email": "manager1@example.com",
                "role": User.Role.MANAGER,
                "data_scope": User.DataScope.GLOBAL,
                "is_staff": False,
                "is_active": True,
                "first_name": "Алена",
                "last_name": "Менеджер",
                "branch": ekb,
            },
        )
        if manager_created:
            manager.set_password("manager12345")
            manager.save()

        demo_company, created = Company.objects.get_or_create(
            inn="6674356036",
            defaults={
                "name": "ООО «Уральский завод теплообменного оборудования»",
                "legal_name": "УЗТО, ООО",
                "kpp": "667901001",
                "address": "Свердловская область, г. Асбест, ул. Промышленная, 17",
                "website": "http://uralzto.ru/",
                "responsible": manager,
                "branch": ekb,
                "raw_fields": {"source": "seed_demo"},
            },
        )

        self.stdout.write(self.style.SUCCESS("Готово."))
        if admin_created:
            self.stdout.write("Demo admin: admin / admin12345 (доступ к /admin/)")
        if manager_created:
            self.stdout.write("Demo manager: manager1 / manager12345 (только UI, без /admin/)")
        self.stdout.write(f"Demo company: {demo_company.name} (открой: /companies/{demo_company.id}/)")


