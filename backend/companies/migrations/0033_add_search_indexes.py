# Generated manually for search performance optimization
# Добавляет GIN индексы с триграммами для быстрого поиска по текстовым полям

from django.contrib.postgres.operations import TrigramExtension, BtreeGinExtension
from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import migrations
from django.db.models.functions import Upper


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0032_company_work_schedule'),
    ]

    operations = [
        # Включаем расширения PostgreSQL для триграммного поиска
        TrigramExtension(),
        BtreeGinExtension(),
        
        # GIN индексы с триграммами для основных полей поиска
        # Используем OpClass(Upper(...)) для поддержки icontains (case-insensitive)
        migrations.AddIndex(
            model_name='company',
            index=GinIndex(
                OpClass(Upper('name'), name='gin_trgm_ops'),
                name='company_name_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='company',
            index=GinIndex(
                OpClass(Upper('legal_name'), name='gin_trgm_ops'),
                name='company_legal_name_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='company',
            index=GinIndex(
                OpClass(Upper('address'), name='gin_trgm_ops'),
                name='company_address_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='company',
            index=GinIndex(
                OpClass(Upper('inn'), name='gin_trgm_ops'),
                name='company_inn_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='company',
            index=GinIndex(
                OpClass(Upper('phone'), name='gin_trgm_ops'),
                name='company_phone_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='company',
            index=GinIndex(
                OpClass(Upper('email'), name='gin_trgm_ops'),
                name='company_email_upper_trgm_gin_idx',
            ),
        ),
        
        # GIN индексы для связанных таблиц (телефоны и email)
        migrations.AddIndex(
            model_name='companyphone',
            index=GinIndex(
                OpClass(Upper('value'), name='gin_trgm_ops'),
                name='companyphone_value_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='companyemail',
            index=GinIndex(
                OpClass(Upper('value'), name='gin_trgm_ops'),
                name='companyemail_value_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='contactphone',
            index=GinIndex(
                OpClass(Upper('value'), name='gin_trgm_ops'),
                name='contactphone_value_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='contactemail',
            index=GinIndex(
                OpClass(Upper('value'), name='gin_trgm_ops'),
                name='contactemail_value_upper_trgm_gin_idx',
            ),
        ),
        
        # Индексы для поиска по ФИО контактов
        migrations.AddIndex(
            model_name='contact',
            index=GinIndex(
                OpClass(Upper('first_name'), name='gin_trgm_ops'),
                name='contact_first_name_upper_trgm_gin_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='contact',
            index=GinIndex(
                OpClass(Upper('last_name'), name='gin_trgm_ops'),
                name='contact_last_name_upper_trgm_gin_idx',
            ),
        ),
    ]
