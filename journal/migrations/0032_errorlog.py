from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0031_password_recovery_telegram_username_label'),
    ]

    operations = [
        migrations.CreateModel(
            name='ErrorLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Дата и время')),
                ('level', models.CharField(default='ERROR', max_length=20, verbose_name='Уровень')),
                ('logger_name', models.CharField(blank=True, max_length=255, verbose_name='Источник')),
                ('message', models.TextField(verbose_name='Сообщение')),
                ('exception', models.TextField(blank=True, verbose_name='Трассировка')),
                ('request_id', models.CharField(blank=True, db_index=True, max_length=64, verbose_name='Код ошибки')),
                ('status_code', models.PositiveSmallIntegerField(blank=True, null=True, verbose_name='HTTP-статус')),
                ('method', models.CharField(blank=True, max_length=16, verbose_name='HTTP-метод')),
                ('path', models.CharField(blank=True, max_length=512, verbose_name='Путь запроса')),
                ('user_label', models.CharField(blank=True, max_length=150, verbose_name='Пользователь')),
                ('metadata', models.JSONField(blank=True, default=dict, verbose_name='Дополнительные данные')),
            ],
            options={
                'verbose_name': 'Журнал ошибки',
                'verbose_name_plural': 'Журнал ошибок',
                'ordering': ['-created_at', '-pk'],
            },
        ),
        migrations.AddIndex(
            model_name='errorlog',
            index=models.Index(fields=['-created_at'], name='error_log_created_idx'),
        ),
        migrations.AddIndex(
            model_name='errorlog',
            index=models.Index(fields=['level', '-created_at'], name='error_log_level_idx'),
        ),
    ]
