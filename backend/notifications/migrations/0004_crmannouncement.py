from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("notifications", "0003_notification_payload"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CrmAnnouncement",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200, verbose_name="Заголовок")),
                ("body", models.TextField(verbose_name="Текст сообщения")),
                ("announcement_type", models.CharField(choices=[("info", "Информация"), ("important", "Важно"), ("urgent", "Срочно")], default="info", max_length=16, verbose_name="Тип")),
                ("created_at", models.DateTimeField(auto_now_add=True, verbose_name="Создано")),
                ("is_active", models.BooleanField(default=True, verbose_name="Активно")),
                ("scheduled_at", models.DateTimeField(blank=True, help_text="Если пусто — показывается сразу", null=True, verbose_name="Показать с")),
                ("created_by", models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="created_announcements", to=settings.AUTH_USER_MODEL, verbose_name="Автор")),
            ],
            options={"verbose_name": "Объявление CRM", "verbose_name_plural": "Объявления CRM", "ordering": ["-created_at"]},
        ),
        migrations.CreateModel(
            name="CrmAnnouncementRead",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("read_at", models.DateTimeField(auto_now_add=True, verbose_name="Прочитано")),
                ("announcement", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reads", to="notifications.crmannouncement", verbose_name="Объявление")),
                ("user", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="announcement_reads", to=settings.AUTH_USER_MODEL, verbose_name="Пользователь")),
            ],
            options={"verbose_name": "Прочтение объявления", "verbose_name_plural": "Прочтения объявлений"},
        ),
        migrations.AddConstraint(
            model_name="crmannouncementread",
            constraint=models.UniqueConstraint(fields=["user", "announcement"], name="uniq_announcement_read"),
        ),
    ]
