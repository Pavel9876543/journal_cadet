from django.db import migrations, models


def normalize_music_education(apps, schema_editor):
    for model_name in ('Student', 'StudentEnrollment', 'CourseApplication'):
        model = apps.get_model('journal', model_name)
        model.objects.filter(music_education='none').update(music_education='self_taught')


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0019_remove_student_subject_specialty'),
    ]

    operations = [
        migrations.RunPython(normalize_music_education, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='courseapplication',
            name='music_education',
            field=models.CharField(
                choices=[
                    ('self_taught', 'Самоучка'),
                    ('basic', 'Музыкальная школа'),
                    ('secondary', 'Колледж'),
                    ('higher', 'Институт'),
                ],
                default='self_taught',
                max_length=20,
                verbose_name='Музыкальное образование',
            ),
        ),
        migrations.AlterField(
            model_name='student',
            name='music_education',
            field=models.CharField(
                blank=True,
                choices=[
                    ('self_taught', 'Самоучка'),
                    ('basic', 'Музыкальная школа'),
                    ('secondary', 'Колледж'),
                    ('higher', 'Институт'),
                ],
                max_length=20,
                verbose_name='Музыкальное образование',
            ),
        ),
        migrations.AlterField(
            model_name='studentenrollment',
            name='music_education',
            field=models.CharField(
                blank=True,
                choices=[
                    ('self_taught', 'Самоучка'),
                    ('basic', 'Музыкальная школа'),
                    ('secondary', 'Колледж'),
                    ('higher', 'Институт'),
                ],
                max_length=20,
                verbose_name='Музыкальное образование',
            ),
        ),
        migrations.AlterField(
            model_name='courseregistrationsettings',
            name='minimum_registration_age',
            field=models.PositiveSmallIntegerField(
                default=14,
                help_text='Допускаются ученики, которым в год начала курсов исполнится указанный возраст.',
                verbose_name='Минимальный возраст для регистрации',
            ),
        ),
    ]
