from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0006_company_documents"),
    ]

    operations = [
        migrations.AddField(
            model_name="companynote",
            name="attachment",
            field=models.FileField(blank=True, null=True, upload_to="company_notes/%Y/%m/%d/", verbose_name="Файл (вложение)"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="attachment_name",
            field=models.CharField(blank=True, default="", max_length=255, verbose_name="Имя файла"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="attachment_ext",
            field=models.CharField(blank=True, db_index=True, default="", max_length=16, verbose_name="Расширение"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="attachment_size",
            field=models.BigIntegerField(default=0, verbose_name="Размер (байт)"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="attachment_content_type",
            field=models.CharField(blank=True, default="", max_length=120, verbose_name="MIME тип"),
        ),
        migrations.DeleteModel(
            name="CompanyDocument",
        ),
    ]


