from django.db import migrations, models
from django.db.models import F


def forward(apps, schema_editor):
    Company = apps.get_model("companies", "Company")
    # Для уже существующих записей: считаем "создателем" ответственного (если он есть).
    Company.objects.filter(created_by__isnull=True, responsible__isnull=False).update(created_by=F("responsible"))


def backward(apps, schema_editor):
    # Не откатываем данные "создателя" автоматически.
    return


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0003_seed_statuses_and_spheres"),
    ]

    operations = [
        migrations.AddField(
            model_name="company",
            name="created_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name="created_companies",
                to="accounts.user",
                verbose_name="Создатель",
            ),
        ),
        migrations.RunPython(forward, backward),
    ]


