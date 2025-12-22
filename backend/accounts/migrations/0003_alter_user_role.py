from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_alter_branch_code_alter_branch_name_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="user",
            name="role",
            field=models.CharField(
                choices=[
                    ("manager", "Менеджер"),
                    ("branch_director", "Директор филиала"),
                    ("sales_head", "Руководитель отдела продаж"),
                    ("group_manager", "Управляющий группой компаний"),
                    ("admin", "Администратор"),
                ],
                default="manager",
                max_length=32,
                verbose_name="Роль",
            ),
        ),
    ]


