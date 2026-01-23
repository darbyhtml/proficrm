from django.db import migrations, models


def backfill_attachment_original_name(apps, schema_editor):
    Campaign = apps.get_model("mailer", "Campaign")
    qs = Campaign.objects.exclude(attachment="").exclude(attachment__isnull=True)
    for c in qs.only("id", "attachment", "attachment_original_name"):
        if (c.attachment_original_name or "").strip():
            continue
        # В миграциях attachment представлен как FieldFile, имя пути лежит в attachment.name
        att_name = ""
        try:
            att_name = (getattr(getattr(c, "attachment", None), "name", "") or "").strip()
        except Exception:
            att_name = ""
        if not att_name:
            continue
        base = att_name.split("/")[-1] if "/" in att_name else att_name
        Campaign.objects.filter(id=c.id).update(attachment_original_name=base[:255])


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0017_rename_mailer_camp_status_priority_idx_mailer_camp_status_5db2fb_idx_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="campaign",
            name="attachment_original_name",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Имя файла при загрузке (используется при отправке, чтобы не переименовывать вложение).",
                max_length=255,
                verbose_name="Оригинальное имя вложения",
            ),
        ),
        migrations.RunPython(backfill_attachment_original_name, migrations.RunPython.noop),
    ]

