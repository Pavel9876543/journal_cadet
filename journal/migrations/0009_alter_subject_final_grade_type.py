from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0008_subject_students'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subject',
            name='final_grade_type',
            field=models.CharField(choices=[('numeric', 'Пятибалльная (1-5, Н)'), ('pass_fail', 'Зачет/незачет (зачет, незачет)')], default='numeric', max_length=20, verbose_name='Тип итоговой оценки'),
        ),
    ]
