# Generated manually

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('companies', '0026_merge_0024_0025'),
    ]

    operations = [
        migrations.CreateModel(
            name='CompanyEmail',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('value', models.EmailField(db_index=True, max_length=254, verbose_name='Email')),
                ('order', models.IntegerField(db_index=True, default=0, verbose_name='Порядок')),
                ('company', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='emails', to='companies.company', verbose_name='Компания')),
            ],
            options={
                'ordering': ['order', 'value'],
            },
        ),
        migrations.AddIndex(
            model_name='companyemail',
            index=models.Index(fields=['value'], name='companies_c_value_idx'),
        ),
        migrations.AddIndex(
            model_name='companyemail',
            index=models.Index(fields=['company', 'order'], name='companies_c_company_order_idx'),
        ),
    ]
