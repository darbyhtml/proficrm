from django.db import migrations, models


def backfill_attachment_original_name(apps, schema_editor):
    Campaign = apps.get_model("mailer", "Campaign")
    qs = Campaign.objects.exclude(attachment="").exclude(attachment__isnull=True)
    for c in qs.only("id", "attachment", "attachment_original_name"):
        if (c.attachment_original_name or "").strip():
            continue
        att = (c.attachment or "").strip()
        # attachment хранится как относительный путь в storage
        base = att.split("/")[-1] if "/" in att else att
        Campaign.objects.filter(id=c.id).update(attachment_original_name=base[:255])


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0010_email_cooldown"),
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

