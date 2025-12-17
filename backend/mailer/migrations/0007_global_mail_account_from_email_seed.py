from django.db import migrations


def forward(apps, schema_editor):
    GlobalMailAccount = apps.get_model("mailer", "GlobalMailAccount")
    obj = GlobalMailAccount.objects.filter(id=1).first()
    if not obj:
        return
    if not (obj.from_email or "").strip():
        obj.from_email = "no-reply@groupprofi.ru"
        obj.save(update_fields=["from_email"])


def backward(apps, schema_editor):
    return


class Migration(migrations.Migration):
    dependencies = [
        ("mailer", "0006_global_mail_account_from_email"),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]


