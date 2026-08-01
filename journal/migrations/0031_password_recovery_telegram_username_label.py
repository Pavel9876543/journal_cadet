from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0030_performance_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='passwordrecoverycontact',
            name='messenger_username',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Укажите имя Telegram без символа @. Для Telegram оно имеет приоритет '
                    'над номером телефона; для остальных мессенджеров всегда используется телефон.'
                ),
                max_length=100,
                verbose_name='Имя пользователя в Telegram',
            ),
        ),
    ]
