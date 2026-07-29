from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0021_alter_courseapplication_instrument'),
    ]

    operations = [
        migrations.AddField(
            model_name='courseregistrationsettings',
            name='application_limit',
            field=models.PositiveIntegerField(
                blank=True,
                help_text='Отклонённые заявки в лимите не учитываются.',
                null=True,
                verbose_name='Лимит зарегистрированных учеников',
            ),
        ),
        migrations.AddField(
            model_name='courseregistrationsettings',
            name='registration_mode',
            field=models.CharField(
                choices=[
                    ('open', 'Открыта вручную (лимит не учитывается)'),
                    ('automatic', 'Автоматически до достижения лимита'),
                    ('closed', 'Завершена вручную'),
                ],
                default='open',
                help_text=(
                    'Ручное открытие и завершение имеют приоритет над лимитом. '
                    'В автоматическом режиме регистрация завершится при достижении лимита.'
                ),
                max_length=16,
                verbose_name='Режим регистрации',
            ),
        ),
    ]
