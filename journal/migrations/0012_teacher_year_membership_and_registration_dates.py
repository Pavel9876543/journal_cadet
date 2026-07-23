from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def populate_teacher_year_memberships(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    Teacher = apps.get_model('journal', 'Teacher')
    TeacherEnrollment = apps.get_model('journal', 'TeacherEnrollment')
    GroupSubject = apps.get_model('journal', 'GroupSubject')
    StudentSubject = apps.get_model('journal', 'StudentSubject')

    pairs = set(
        GroupSubject.objects.values_list('teacher_id', 'group__academic_year_id')
    )
    pairs.update(
        StudentSubject.objects.values_list('teacher_id', 'academic_year_id')
    )

    active_year_id = (
        AcademicYear.objects.filter(is_active=True).values_list('pk', flat=True).first()
    )
    if active_year_id:
        pairs.update(
            (teacher_id, active_year_id)
            for teacher_id in Teacher.objects.filter(is_active=True).values_list('pk', flat=True)
        )

    TeacherEnrollment.objects.bulk_create(
        [
            TeacherEnrollment(
                teacher_id=teacher_id,
                academic_year_id=academic_year_id,
                is_active=True,
            )
            for teacher_id, academic_year_id in sorted(pairs)
            if teacher_id and academic_year_id
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('journal', '0011_courseapplication_unique_phone_per_year'),
    ]

    operations = [
        migrations.CreateModel(
            name='TeacherEnrollment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен в учебном году')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Создано')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Изменено')),
                ('academic_year', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='teacher_enrollments', to='journal.academicyear', verbose_name='Учебный год')),
                ('teacher', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='academic_year_memberships', to='journal.teacher', verbose_name='Преподаватель')),
            ],
            options={
                'verbose_name': 'Участие преподавателя в учебном году',
                'verbose_name_plural': 'Участие преподавателей в учебных годах',
                'ordering': ['-academic_year__starts_on', 'teacher__full_name'],
                'indexes': [
                    models.Index(fields=['academic_year', 'teacher'], name='teacher_year_membership_idx'),
                    models.Index(fields=['is_active'], name='teacher_year_active_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(fields=('teacher', 'academic_year'), name='unique_teacher_academic_year'),
                ],
            },
        ),
        migrations.RunPython(populate_teacher_year_memberships, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='courseapplication',
            name='student',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='course_applications', to='journal.student', verbose_name='Ученик в журнале'),
        ),
        migrations.AlterField(
            model_name='courseapplication',
            name='user',
            field=models.ForeignKey(blank=True, editable=False, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='course_applications', to=settings.AUTH_USER_MODEL, verbose_name='Пользователь ученика'),
        ),
        migrations.RemoveField(
            model_name='courseregistrationsettings',
            name='course_ends_on',
        ),
        migrations.RemoveField(
            model_name='courseregistrationsettings',
            name='course_starts_on',
        ),
    ]
