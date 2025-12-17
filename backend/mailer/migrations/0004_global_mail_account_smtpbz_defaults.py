from django.db import migrations


def forward(apps, schema_editor):
    GlobalMailAccount = apps.get_model("mailer", "GlobalMailAccount")
    obj = GlobalMailAccount.objects.filter(id=1).first()
    if not obj:
        return

    # If admin hasn't configured anything yet (still default-ish), prefill for smtp.bz.
    is_blank = (obj.smtp_username or "").strip() == "" and (obj.smtp_password_enc or "").strip() == ""
    is_yandex_default = (obj.smtp_host or "").strip() == "smtp.yandex.ru"
    if is_blank and is_yandex_default:
        obj.smtp_host = "connect.smtp.bz"
        obj.smtp_port = 587
        obj.use_starttls = True
        obj.rate_per_minute = 1
        obj.rate_per_day = 15000
        obj.save(update_fields=["smtp_host", "smtp_port", "use_starttls", "rate_per_minute", "rate_per_day", "updated_at"])


def backward(apps, schema_editor):
    # No-op: don't revert user/admin configuration.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0003_global_mail_account"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]


