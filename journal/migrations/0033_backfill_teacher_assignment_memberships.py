from django.db import migrations


def backfill_teacher_assignment_memberships(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    AssessmentItem = apps.get_model('journal', 'AssessmentItem')
    GroupSubject = apps.get_model('journal', 'GroupSubject')
    StudentSubject = apps.get_model('journal', 'StudentSubject')
    Teacher = apps.get_model('journal', 'Teacher')
    TeacherEnrollment = apps.get_model('journal', 'TeacherEnrollment')
    UserAcademicYearMembership = apps.get_model(
        'journal',
        'UserAcademicYearMembership',
    )

    pairs = set(
        GroupSubject.objects.filter(is_active=True).values_list(
            'teacher_id',
            'group__academic_year_id',
        )
    )
    pairs.update(
        StudentSubject.objects.filter(is_active=True).values_list(
            'teacher_id',
            'academic_year_id',
        )
    )
    pairs.update(
        AssessmentItem.objects.filter(
            is_active=True,
            responsible_teacher_id__isnull=False,
        ).values_list(
            'responsible_teacher_id',
            'academic_year_id',
        )
    )

    active_year_ids = set(
        AcademicYear.objects.filter(is_active=True).values_list('pk', flat=True)
    )
    teacher_user_ids = dict(
        Teacher.objects.filter(pk__in={teacher_id for teacher_id, _ in pairs})
        .values_list('pk', 'user_id')
    )

    active_teacher_ids = set()
    for teacher_id, academic_year_id in pairs:
        if not teacher_id or not academic_year_id:
            continue
        TeacherEnrollment.objects.update_or_create(
            teacher_id=teacher_id,
            academic_year_id=academic_year_id,
            defaults={'is_active': True},
        )
        user_id = teacher_user_ids.get(teacher_id)
        if user_id:
            UserAcademicYearMembership.objects.update_or_create(
                user_id=user_id,
                academic_year_id=academic_year_id,
                defaults={'is_active': True},
            )
        if academic_year_id in active_year_ids:
            active_teacher_ids.add(teacher_id)

    if active_teacher_ids:
        Teacher.objects.filter(pk__in=active_teacher_ids).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0032_errorlog'),
    ]

    operations = [
        migrations.RunPython(
            backfill_teacher_assignment_memberships,
            migrations.RunPython.noop,
        ),
    ]
