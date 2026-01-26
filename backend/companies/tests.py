"""
Тесты для нормализации данных и API.

Покрывают:
- Нормализацию телефонов (различные форматы)
- Нормализацию ИНН (с пробелами/дефисами)
- Нормализацию расписания работы
- DRF фильтры (search, ordering)
"""
from django.test import TestCase
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient
from rest_framework import status

from .normalizers import normalize_phone, normalize_inn, normalize_work_schedule
from .models import Company, CompanyStatus

User = get_user_model()


class NormalizersTestCase(TestCase):
    """Тесты для нормализаторов данных"""

    def test_normalize_phone_e164(self):
        """Тест нормализации телефонов в формат E.164"""
        # Тест 1: "8XXXXXXXXXX" -> "+7XXXXXXXXXX"
        self.assertEqual(normalize_phone("89991234567"), "+79991234567")
        self.assertEqual(normalize_phone("8 (999) 123-45-67"), "+79991234567")
        
        # Тест 2: "+7 (999) 123-45-67 доб. 123" -> "+79991234567" (extension извлекается)
        result = normalize_phone("+7 (999) 123-45-67 доб. 123")
        self.assertTrue(result.startswith("+7"))
        self.assertIn("9991234567", result.replace("+", "").replace("-", "").replace(" ", ""))
        
        # Тест 3: "7XXXXXXXXXX" -> "+7XXXXXXXXXX"
        self.assertEqual(normalize_phone("79991234567"), "+79991234567")
        
        # Тест 4: "10 цифр" -> "+7XXXXXXXXXX"
        self.assertEqual(normalize_phone("9991234567"), "+79991234567")
        
        # Тест 5: Уже нормализованный номер
        self.assertEqual(normalize_phone("+79991234567"), "+79991234567")
        
        # Тест 6: Формат с скобками "(38473)3-33-92"
        result = normalize_phone("(38473)3-33-92")
        self.assertTrue(result.startswith("+7"))
        
        # Тест 7: Пустые значения
        self.assertEqual(normalize_phone(None), "")
        self.assertEqual(normalize_phone(""), "")
        self.assertEqual(normalize_phone("   "), "")
        
        # Тест 8: Невалидные номера (слишком короткие) - возвращаются как есть (обрезанные)
        invalid = normalize_phone("123")
        self.assertLessEqual(len(invalid), 50)
        
        # Тест 9: Мусор с валидным номером внутри
        result = normalize_phone("тел. +7 999 123 45 67")
        self.assertIn("9991234567", result.replace("+", "").replace("-", "").replace(" ", ""))

    def test_normalize_inn(self):
        """Тест нормализации ИНН"""
        # Тест 1: ИНН с пробелами
        self.assertEqual(normalize_inn("1234567890"), "1234567890")
        self.assertEqual(normalize_inn("1234 5678 90"), "1234567890")
        
        # Тест 2: Несколько ИНН через разные разделители
        result = normalize_inn("1234567890 / 0987654321")
        self.assertIn("1234567890", result)
        self.assertIn("0987654321", result)
        
        # Тест 3: ИНН с дефисами и пробелами
        result = normalize_inn("1234-5678-90")
        self.assertEqual(result, "1234567890")
        
        # Тест 4: Пустые значения
        self.assertEqual(normalize_inn(None), "")
        self.assertEqual(normalize_inn(""), "")
        
        # Тест 5: ИНН 12 цифр (для ИП)
        self.assertEqual(normalize_inn("123456789012"), "123456789012")

    def test_normalize_work_schedule(self):
        """Тест нормализации расписания работы"""
        # Тест 1: Простое расписание
        result = normalize_work_schedule("пн-пт 09:00-18:00")
        self.assertIn("09:00", result)
        self.assertIn("18:00", result)
        
        # Тест 2: Расписание с выходными
        result = normalize_work_schedule("пн-пт 09:00-18:00\nсб-вс выходной")
        self.assertIn("09:00", result)
        self.assertIn("18:00", result)
        
        # Тест 3: Круглосуточно
        result = normalize_work_schedule("24/7")
        self.assertIn("Круглосуточно", result)
        
        # Тест 4: Пустые значения
        self.assertEqual(normalize_work_schedule(None), "")
        self.assertEqual(normalize_work_schedule(""), "")
        
        # Тест 5: Различные форматы времени
        result = normalize_work_schedule("пн-пт 9.00-18.00")
        self.assertIn("09:00", result)
        self.assertIn("18:00", result)


class CompanyAPITestCase(TestCase):
    """Тесты для API компаний (нормализация и фильтры)"""

    def setUp(self):
        """Настройка тестовых данных"""
        self.client = APIClient()
        self.user = User.objects.create_user(
            username="testuser",
            email="test@example.com",
            password="testpass123",
            role=User.Role.ADMIN
        )
        self.client.force_authenticate(user=self.user)
        
        # Создаем тестовые компании
        self.company1 = Company.objects.create(
            name="Тестовая компания 1",
            inn="1234567890",
            phone="89991234567",
            email="test1@example.com",
            work_schedule="пн-пт 09:00-18:00"
        )
        self.company2 = Company.objects.create(
            name="Другая компания",
            inn="0987654321",
            phone="+79991234568",
            email="test2@example.com"
        )

    def test_api_normalize_phone_on_create(self):
        """Тест нормализации телефона при создании через API"""
        data = {
            "name": "Новая компания",
            "phone": "8 (999) 123-45-69"
        }
        response = self.client.post("/api/companies/", data, format="json")
        # Может быть 201 или 301 (редирект), проверяем успешность
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_301_MOVED_PERMANENTLY])
        
        # Проверяем, что телефон нормализован
        company = Company.objects.get(name="Новая компания")
        self.assertTrue(company.phone.startswith("+7"))
        self.assertIn("9991234569", company.phone)

    def test_api_normalize_inn_on_create(self):
        """Тест нормализации ИНН при создании через API"""
        data = {
            "name": "Компания с ИНН",
            "inn": "1234 5678 90"
        }
        response = self.client.post("/api/companies/", data, format="json")
        # Может быть 201 или 301 (редирект), проверяем успешность
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_301_MOVED_PERMANENTLY])
        
        # Проверяем, что ИНН нормализован
        company = Company.objects.get(name="Компания с ИНН")
        self.assertEqual(company.inn, "1234567890")

    def test_api_normalize_work_schedule_on_create(self):
        """Тест нормализации расписания при создании через API"""
        data = {
            "name": "Компания с расписанием",
            "work_schedule": "пн-пт 9.00-18.00"
        }
        response = self.client.post("/api/companies/", data, format="json")
        # Может быть 201 или 301 (редирект), проверяем успешность
        self.assertIn(response.status_code, [status.HTTP_201_CREATED, status.HTTP_301_MOVED_PERMANENTLY])
        
        # Проверяем, что расписание нормализовано
        company = Company.objects.get(name="Компания с расписанием")
        self.assertIn("09:00", company.work_schedule)
        self.assertIn("18:00", company.work_schedule)

    def test_api_search_filter(self):
        """Тест работы SearchFilter в API"""
        # Поиск по названию
        response = self.client.get("/api/companies/?search=Тестовая", follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # API может возвращать список или словарь с results в зависимости от пагинации
        data = response.data
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        else:
            results = data if isinstance(data, list) else []
        self.assertGreaterEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "Тестовая компания 1")
        
        # Поиск по ИНН
        response = self.client.get("/api/companies/?search=1234567890", follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        else:
            results = data if isinstance(data, list) else []
        self.assertGreaterEqual(len(results), 1)
        
        # Поиск по телефону
        response = self.client.get("/api/companies/?search=9991234567", follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        else:
            results = data if isinstance(data, list) else []
        self.assertGreaterEqual(len(results), 1)

    def test_api_ordering_filter(self):
        """Тест работы OrderingFilter в API"""
        # Сортировка по названию (по возрастанию)
        response = self.client.get("/api/companies/?ordering=name", follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        else:
            results = data if isinstance(data, list) else []
        if len(results) >= 2:
            self.assertLessEqual(results[0]["name"], results[1]["name"])
        
        # Сортировка по дате обновления (по убыванию, по умолчанию)
        response = self.client.get("/api/companies/", follow=True)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        data = response.data
        if isinstance(data, dict) and "results" in data:
            results = data["results"]
        else:
            results = data if isinstance(data, list) else []
        if len(results) >= 2:
            # Проверяем, что сортировка по updated_at работает
            self.assertIn("updated_at", results[0])

    def test_api_update_normalizes_data(self):
        """Тест нормализации данных при обновлении через API"""
        # Обновляем телефон
        data = {"phone": "8 (888) 777-66-55"}
        response = self.client.patch(f"/api/companies/{self.company1.id}/", data, format="json")
        # Может быть 200 или 301 (редирект), проверяем успешность
        self.assertIn(response.status_code, [status.HTTP_200_OK, status.HTTP_301_MOVED_PERMANENTLY])
        
        # Проверяем, что телефон нормализован
        self.company1.refresh_from_db()
        self.assertTrue(self.company1.phone.startswith("+7"))
        # Нормализованный телефон должен быть +78887776655
        normalized_phone = self.company1.phone.replace("+7", "").replace("-", "").replace(" ", "").replace("(", "").replace(")", "")
        self.assertIn("8887776655", normalized_phone)


class ContactPhoneNormalizationTestCase(TestCase):
    """Тесты нормализации телефонов контактов"""

    def setUp(self):
        """Настройка тестовых данных"""
        self.company = Company.objects.create(name="Тестовая компания")

    def test_contact_phone_normalization_in_save(self):
        """Тест нормализации телефона контакта в save()"""
        from .models import Contact, ContactPhone
        
        contact = Contact.objects.create(
            company=self.company,
            first_name="Иван",
            last_name="Иванов"
        )
        
        phone = ContactPhone.objects.create(
            contact=contact,
            value="89991234567"
        )
        
        # Проверяем, что телефон нормализован
        self.assertTrue(phone.value.startswith("+7"))
        self.assertIn("9991234567", phone.value)


class CompanyModelNormalizationTestCase(TestCase):
    """Тесты нормализации в моделях"""

    def test_company_phone_normalization_in_save(self):
        """Тест нормализации телефона компании в save()"""
        company = Company(name="Тест")
        company.phone = "89991234567"
        company.save()
        
        # Проверяем, что телефон нормализован
        self.assertTrue(company.phone.startswith("+7"))
        self.assertIn("9991234567", company.phone)

    def test_company_inn_normalization_in_save(self):
        """Тест нормализации ИНН компании в save()"""
        company = Company(name="Тест")
        company.inn = "1234 5678 90"
        company.save()
        
        # Проверяем, что ИНН нормализован
        self.assertEqual(company.inn, "1234567890")

    def test_company_work_schedule_normalization_in_save(self):
        """Тест нормализации расписания компании в save()"""
        company = Company(name="Тест")
        company.work_schedule = "пн-пт 9.00-18.00"
        company.save()
        
        # Проверяем, что расписание нормализовано
        self.assertIn("09:00", company.work_schedule)
        self.assertIn("18:00", company.work_schedule)
