from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0016_custom_instruments'),
    ]

    operations = [
        migrations.AddField(
            model_name='subject',
            name='assessment_mode',
            field=models.CharField(
                choices=[
                    ('standard', 'Обычный журнал'),
                    ('elements', 'Сдача произведений / элементов'),
                ],
                default='standard',
                help_text='Специальный режим включается явно и не зависит от названия предмета.',
                max_length=20,
                verbose_name='Режим аттестации',
            ),
        ),
        migrations.AlterField(
            model_name='subject',
            name='final_grade_type',
            field=models.CharField(
                choices=[
                    ('numeric', 'Пятибалльная (1-5, Н)'),
                    ('pass_fail', 'Зачет/незачет'),
                ],
                default='numeric',
                help_text='Используется для подсказок; сами оценки хранятся строками.',
                max_length=20,
                verbose_name='Тип итоговой оценки',
            ),
        ),
        migrations.AlterField(
            model_name='grade',
            name='value',
            field=models.CharField(
                help_text='Произвольное строковое значение, например 5+, 4-, N или Зачет.',
                max_length=64,
                verbose_name='Оценка',
            ),
        ),
        migrations.AlterField(
            model_name='subjectresult',
            name='exam_grade',
            field=models.CharField(blank=True, max_length=64, null=True, verbose_name='Экзамен'),
        ),
        migrations.AlterField(
            model_name='subjectresult',
            name='final_grade',
            field=models.CharField(blank=True, max_length=64, null=True, verbose_name='Итоговая оценка'),
        ),
        migrations.AddIndex(
            model_name='subject',
            index=models.Index(fields=['assessment_mode'], name='subject_assessment_mode_idx'),
        ),
    ]
