from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0034_backfill_assessment_teacher_subjects'),
    ]

    operations = [
        migrations.AlterField(
            model_name='errorlog',
            name='message',
            field=models.TextField(verbose_name='Техническое сообщение'),
        ),
        migrations.AddField(
            model_name='errorlog',
            name='user_message',
            field=models.TextField(
                blank=True,
                default='',
                verbose_name='Сообщение для пользователя',
            ),
        ),
    ]
