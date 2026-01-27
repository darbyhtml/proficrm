from django.db import migrations, models
import django.db.models.deletion


def create_contract_types(apps, schema_editor):
    """Создаем записи для видов договоров"""
    ContractType = apps.get_model("companies", "ContractType")
    
    # Старые типы договоров
    contract_types = [
        ("Рамочный", 0, 14, 7),
        ("Тендер", 1, 14, 7),
        ("Юр. лицо", 2, 14, 7),
        ("Физ. лицо", 3, 14, 7),
    ]
    
    # Новые типы договоров
    new_types = [
        ("Предоплата", 4, 14, 7),
        ("Постоплата", 5, 14, 7),
        ("Счет-оферта", 6, 14, 7),
    ]
    
    for name, order, warning_days, danger_days in contract_types + new_types:
        ContractType.objects.get_or_create(
            name=name,
            defaults={
                "order": order,
                "warning_days": warning_days,
                "danger_days": danger_days,
            }
        )


def migrate_contract_type_data(apps, schema_editor):
    """Переносим данные из старого поля в новое"""
    Company = apps.get_model("companies", "Company")
    ContractType = apps.get_model("companies", "ContractType")
    
    # Маппинг старых значений на новые
    type_mapping = {
        "frame": "Рамочный",
        "tender": "Тендер",
        "legal": "Юр. лицо",
        "individual": "Физ. лицо",
    }
    
    for company in Company.objects.exclude(contract_type_old=""):
        old_value = company.contract_type_old
        if old_value in type_mapping:
            contract_type_name = type_mapping[old_value]
            contract_type = ContractType.objects.filter(name=contract_type_name).first()
            if contract_type:
                company.contract_type = contract_type
                company.save(update_fields=["contract_type"])


def reverse_migrate_contract_type_data(apps, schema_editor):
    """Обратный перенос данных (для отката миграции)"""
    Company = apps.get_model("companies", "Company")
    ContractType = apps.get_model("companies", "ContractType")
    
    # Обратный маппинг
    reverse_mapping = {
        "Рамочный": "frame",
        "Тендер": "tender",
        "Юр. лицо": "legal",
        "Физ. лицо": "individual",
    }
    
    for company in Company.objects.exclude(contract_type=None):
        if company.contract_type:
            contract_type_name = company.contract_type.name
            if contract_type_name in reverse_mapping:
                company.contract_type_old = reverse_mapping[contract_type_name]
                company.save(update_fields=["contract_type_old"])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("companies", "0038_company_region"),
    ]

    operations = [
        # Создаем модель ContractType
        migrations.CreateModel(
            name="ContractType",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField("Название", max_length=120, unique=True)),
                ("warning_days", models.PositiveIntegerField("Дней до желтого предупреждения", default=14, help_text="За сколько дней до окончания договора показывать желтое предупреждение")),
                ("danger_days", models.PositiveIntegerField("Дней до красного предупреждения", default=7, help_text="За сколько дней до окончания договора показывать красное предупреждение")),
                ("order", models.IntegerField("Порядок сортировки", default=0, db_index=True)),
            ],
            options={
                "verbose_name": "Вид договора",
                "verbose_name_plural": "Виды договоров",
                "ordering": ["order", "name"],
            },
        ),
        # Создаем записи для видов договоров
        migrations.RunPython(create_contract_types, reverse_code=noop),
        # Переименовываем старое поле во временное
        migrations.RenameField(
            model_name="company",
            old_name="contract_type",
            new_name="contract_type_old",
        ),
        # Добавляем новое поле как ForeignKey
        migrations.AddField(
            model_name="company",
            name="contract_type",
            field=models.ForeignKey(
                verbose_name="Вид договора",
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="companies",
                db_index=True,
                to="companies.contracttype",
            ),
        ),
        # Переносим данные
        migrations.RunPython(migrate_contract_type_data, reverse_code=reverse_migrate_contract_type_data),
        # Удаляем старое поле
        migrations.RemoveField(
            model_name="company",
            name="contract_type_old",
        ),
    ]
