from django.db import migrations


def seed(apps, schema_editor):
    CompanySphere = apps.get_model("companies", "CompanySphere")
    CompanyStatus = apps.get_model("companies", "CompanyStatus")

    # Сферы деятельности (отрасли/направления)
    spheres = [
        "Авиация",
        "Авто",
        "БПЛА",
        "Банки",
        "ВЭД",
        "Администрация",
        "ДОПОГ",
        "ЖКХ",
        "Культура",
        "Медицина",
        "Металургия",
        "Монтаж ОПС",
        "Нефтянка",
        "Отдых, оздоровление",
        "Охрана труда",
        "Первая помощь",
        "Педагогика",
        "Производство",
        "Сельхоз отрасль",
        "Силовые структуры",
        "Социалка",
        "Спорт",
        "Стройка",
        "Торговля",
        "Горнодобывающая пром",
        "Центры занятости",
        "Экология",
        "Лесохозяйственная деятельность",
        "Система общественного питания",
        "Тренинг",
        "Госзакупки 44-ФЗ",
        "Госзакупки 223-ФЗ",
        "Услуги",
    ]

    # Статусы (стадия/классификация клиента)
    statuses = [
        "Новая",
        "В работе",
        "Коммерческое предложение",
        "Переговоры",
        "Договор",
        "Счёт",
        "Оплачено",
        "Постоянный клиент",
        "Конкурент",
        "Деятельность прекращена",
        "Информация не найдена",
    ]

    for name in spheres:
        CompanySphere.objects.get_or_create(name=name)

    for name in statuses:
        CompanyStatus.objects.get_or_create(name=name)


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0002_alter_company_address_and_more"),
    ]

    operations = [
        migrations.RunPython(seed, reverse_code=noop),
    ]


