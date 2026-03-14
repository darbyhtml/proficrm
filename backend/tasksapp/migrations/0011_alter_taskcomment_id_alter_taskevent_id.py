from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('tasksapp', '0010_taskcomment_taskevent'),
    ]

    operations = [
        migrations.AlterField(
            model_name='taskcomment',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
        migrations.AlterField(
            model_name='taskevent',
            name='id',
            field=models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID'),
        ),
    ]
