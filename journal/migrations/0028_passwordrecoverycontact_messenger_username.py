from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0027_useracademicyearmembership'),
    ]

    operations = [
        migrations.AddField(
            model_name='passwordrecoverycontact',
            name='messenger_username',
            field=models.CharField(
                blank=True,
                help_text=(
                    'Укажите имя без символа @. Оно используется для прямой ссылки, '
                    'когда выбранный мессенджер поддерживает имена пользователей.'
                ),
                max_length=100,
                verbose_name='Имя пользователя в мессенджере',
            ),
        ),
    ]
