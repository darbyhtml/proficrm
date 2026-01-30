"""
Тесты для связки "головная организация / филиал" (поле Company.head_company).

Цели:
- выбор головной компании должен работать даже при AJAX-подгрузке (когда <select> рендерится минимальным);
- нельзя создавать циклы (головная не может быть дочерней карточкой).
"""

from django.contrib.auth import get_user_model
from django.test import Client, TestCase, override_settings

from companies.models import Company

User = get_user_model()


@override_settings(SECURE_SSL_REDIRECT=False)
class CompanyHeadCompanyTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username="manager",
            password="pass12345",
            role=User.Role.MANAGER,
        )
        self.client.force_login(self.user)

        # Компания менеджера, которую он редактирует (филиал клиента)
        self.branch_card = Company.objects.create(name="Филиал клиента", inn="1234567890", responsible=self.user)

        # Потенциальная "головная" карточка (может быть у другого ответственного)
        self.other_user = User.objects.create_user(
            username="other",
            password="pass12345",
            role=User.Role.MANAGER,
        )
        self.head_card = Company.objects.create(name="Головная организация клиента", inn="0987654321", responsible=self.other_user)

    def _company_edit_post_data(self, *, company: Company, head_company_id: str | None):
        """
        CompanyEditForm включает много полей; большинство из них необязательны,
        поэтому достаточно отправить полный набор ключей с текущими значениями/пустыми строками.
        """
        return {
            "name": company.name or "",
            "legal_name": company.legal_name or "",
            "inn": company.inn or "",
            "kpp": company.kpp or "",
            "address": company.address or "",
            "website": company.website or "",
            "activity_kind": company.activity_kind or "",
            "employees_count": company.employees_count or "",
            "workday_start": company.workday_start.isoformat() if company.workday_start else "",
            "workday_end": company.workday_end.isoformat() if company.workday_end else "",
            "work_timezone": company.work_timezone or "",
            "work_schedule": company.work_schedule or "",
            "contract_type": company.contract_type or "",
            "contract_until": company.contract_until.isoformat() if company.contract_until else "",
            "head_company": head_company_id or "",
            "phone": company.phone or "",
            "email": company.email or "",
            "contact_name": company.contact_name or "",
            "contact_position": company.contact_position or "",
            "status": company.status_id or "",
            "spheres": [],
        }

    def test_can_set_head_company_via_ajax_value_not_in_initial_queryset(self):
        """
        Важно: head_company поле валидируется даже если выбранная компания не была в <select> при рендере.
        (Реальный кейс: AJAX-поиск подставляет ID, но сервер должен принять.)
        """
        url = f"/companies/{self.branch_card.id}/edit/"
        resp = self.client.post(url, data=self._company_edit_post_data(company=self.branch_card, head_company_id=str(self.head_card.id)))
        self.assertEqual(resp.status_code, 302)

        self.branch_card.refresh_from_db()
        self.assertEqual(self.branch_card.head_company_id, self.head_card.id)

    def test_cannot_create_cycle_head_company_points_to_descendant(self):
        """
        Если у компании уже есть филиал, нельзя выбрать этот филиал как "головную" (цикл).
        """
        # Делаем организацию "головной", а текущую branch_card — её филиалом
        org_head = Company.objects.create(name="Организация", inn="1111111111", responsible=self.user)
        self.branch_card.head_company = org_head
        self.branch_card.save()

        url = f"/companies/{org_head.id}/edit/"
        resp = self.client.post(url, data=self._company_edit_post_data(company=org_head, head_company_id=str(self.branch_card.id)))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.context["form"].errors.get("head_company"))

        org_head.refresh_from_db()
        self.assertIsNone(org_head.head_company_id)

    def test_company_autocomplete_excludes_requested_id(self):
        url = "/companies/autocomplete/"
        resp = self.client.get(url, {"q": "Гол", "exclude": str(self.head_card.id)})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        ids = {it["id"] for it in data.get("items", [])}
        self.assertNotIn(str(self.head_card.id), ids)

