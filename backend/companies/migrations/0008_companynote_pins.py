from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("companies", "0007_note_attachments_and_remove_company_docs"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="companynote",
            name="is_pinned",
            field=models.BooleanField(db_index=True, default=False, verbose_name="Закреплено"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="pinned_at",
            field=models.DateTimeField(blank=True, null=True, verbose_name="Когда закрепили"),
        ),
        migrations.AddField(
            model_name="companynote",
            name="pinned_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="pinned_company_notes",
                to=settings.AUTH_USER_MODEL,
                verbose_name="Кто закрепил",
            ),
        ),
    ]


