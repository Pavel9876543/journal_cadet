from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('journal', '0007_subject_final_grade_type_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='subject',
            name='students',
            field=models.ManyToManyField(blank=True, related_name='individual_subjects', to='journal.student', verbose_name='Индивидуальные ученики'),
        ),
    ]
