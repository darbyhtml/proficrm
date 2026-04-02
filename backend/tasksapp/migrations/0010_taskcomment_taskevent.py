# Generated manually

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasksapp', '0009_task_is_urgent'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TaskComment',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.TextField(verbose_name='Текст')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('author', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='task_comments',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Автор',
                )),
                ('task', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='comments',
                    to='tasksapp.task',
                )),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
        migrations.CreateModel(
            name='TaskEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(
                    choices=[
                        ('created', 'Создана'),
                        ('status_changed', 'Статус изменён'),
                        ('assigned', 'Переназначена'),
                        ('deadline_changed', 'Дедлайн изменён'),
                    ],
                    max_length=32,
                    verbose_name='Тип события',
                )),
                ('old_value', models.CharField(blank=True, default='', max_length=255, verbose_name='Старое значение')),
                ('new_value', models.CharField(blank=True, default='', max_length=255, verbose_name='Новое значение')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='task_events',
                    to=settings.AUTH_USER_MODEL,
                    verbose_name='Кто изменил',
                )),
                ('task', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='events',
                    to='tasksapp.task',
                )),
            ],
            options={
                'ordering': ['created_at'],
            },
        ),
    ]
