from django.db import migrations


def correct_active_year_memberships(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    GroupSubject = apps.get_model('journal', 'GroupSubject')
    StudentSubject = apps.get_model('journal', 'StudentSubject')
    Teacher = apps.get_model('journal', 'Teacher')
    TeacherEnrollment = apps.get_model('journal', 'TeacherEnrollment')

    active_year_id = (
        AcademicYear.objects
        .filter(is_active=True)
        .values_list('pk', flat=True)
        .first()
    )
    if active_year_id is None:
        return

    active_teacher_ids = set(
        Teacher.objects
        .filter(is_active=True)
        .values_list('pk', flat=True)
    )
    active_teacher_ids.update(
        GroupSubject.objects
        .filter(group__academic_year_id=active_year_id, is_active=True)
        .values_list('teacher_id', flat=True)
    )
    active_teacher_ids.update(
        StudentSubject.objects
        .filter(academic_year_id=active_year_id, is_active=True)
        .values_list('teacher_id', flat=True)
    )
    active_teacher_ids.discard(None)

    memberships = TeacherEnrollment.objects.filter(academic_year_id=active_year_id)
    memberships.update(is_active=False)
    if active_teacher_ids:
        memberships.filter(teacher_id__in=active_teacher_ids).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0012_teacher_year_membership_and_registration_dates'),
    ]

    operations = [
        migrations.RunPython(
            correct_active_year_memberships,
            migrations.RunPython.noop,
        ),
    ]
