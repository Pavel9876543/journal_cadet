from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0013_correct_teacher_membership_activity'),
    ]

    operations = [
        migrations.AlterField(
            model_name='courseapplication',
            name='status',
            field=models.CharField(
                choices=[
                    ('confirmed', 'Подтверждена'),
                    ('rejected', 'Отклонена'),
                ],
                default='confirmed',
                help_text=(
                    'При отклонении удаляются только неиспользуемые записи этого '
                    'учебного года; общий аккаунт и данные прошлых лет сохраняются.'
                ),
                max_length=20,
                verbose_name='Статус заявки',
            ),
        ),
    ]
