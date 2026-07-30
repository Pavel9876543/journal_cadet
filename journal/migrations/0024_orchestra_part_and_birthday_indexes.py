from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0023_registration_settings_by_year_and_account_profile'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseapplication',
            name='orchestra_part',
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name='Партия в оркестре',
            ),
        ),
        migrations.AddField(
            model_name='student',
            name='orchestra_part',
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name='Партия в оркестре',
            ),
        ),
        migrations.AddField(
            model_name='studentenrollment',
            name='orchestra_part',
            field=models.CharField(
                blank=True,
                max_length=255,
                verbose_name='Партия в оркестре',
            ),
        ),
        migrations.AlterField(
            model_name='courseapplication',
            name='instrument',
            field=models.CharField(
                help_text='Отображаемое значение, синхронизированное со структурированными полями.',
                max_length=255,
                verbose_name='Музыкальный инструмент',
            ),
        ),
        migrations.AlterField(
            model_name='accountprofile',
            name='birth_date',
            field=models.DateField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name='Дата рождения',
            ),
        ),
        migrations.AlterField(
            model_name='teacher',
            name='birth_date',
            field=models.DateField(
                blank=True,
                db_index=True,
                null=True,
                verbose_name='Дата рождения',
            ),
        ),
    ]
