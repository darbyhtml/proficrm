from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Contact = apps.get_model("companies", "Contact")

    # 1) По умолчанию считаем существующие карточки тёплыми,
    # чтобы "всё стало холодным" не происходило внезапно.
    Company.objects.all().update(lead_state="warm")

    # 2) Но если по компании уже есть хоть одна холодная отметка (контакт или основной контакт),
    # то логично считать карточку холодной.
    cold_company_ids = set(
        Company.objects.filter(primary_contact_is_cold_call=True).values_list("id", flat=True)
    )
    cold_company_ids.update(
        Contact.objects.filter(company_id__isnull=False, is_cold_call=True).values_list("company_id", flat=True).distinct()
    )
    # legacy company-level флаг (устар.) тоже учитываем, если где-то остался
    cold_company_ids.update(
        Company.objects.filter(is_cold_call=True).values_list("id", flat=True)
    )
    if cold_company_ids:
        Company.objects.filter(id__in=list(cold_company_ids)).update(lead_state="cold")


def backwards(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    # Возврат: просто сбросим в warm
    Company.objects.all().update(lead_state="warm")


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0018_alter_company_lead_state"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]


