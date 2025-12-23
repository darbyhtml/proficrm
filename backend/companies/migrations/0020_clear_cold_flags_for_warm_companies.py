from django.db import migrations


def forwards(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    Contact = apps.get_model("companies", "Contact")

    warm_ids = list(Company.objects.filter(lead_state="warm").values_list("id", flat=True))
    if not warm_ids:
        return

    # Для тёплых карточек текущие "холодные" флаги должны быть сняты.
    # Историю не трогаем (CallRequest.is_cold_call и поля marked_* остаются).
    Company.objects.filter(id__in=warm_ids).update(primary_contact_is_cold_call=False)
    Contact.objects.filter(company_id__in=warm_ids).update(is_cold_call=False)


def backwards(apps, schema_editor):
    # Backwards не восстанавливаем — это сознательная нормализация UI-флагов.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0019_backfill_company_lead_state"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]


