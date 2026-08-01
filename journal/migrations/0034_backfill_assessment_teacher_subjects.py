from django.db import migrations


def backfill_assessment_teacher_subjects(apps, schema_editor):
    AssessmentItem = apps.get_model('journal', 'AssessmentItem')
    TeacherSubject = apps.get_model('journal', 'TeacherSubject')

    pairs = AssessmentItem.objects.filter(
        responsible_teacher_id__isnull=False,
    ).values_list('responsible_teacher_id', 'subject_id').distinct()
    TeacherSubject.objects.bulk_create(
        [
            TeacherSubject(teacher_id=teacher_id, subject_id=subject_id)
            for teacher_id, subject_id in pairs
            if teacher_id and subject_id
        ],
        ignore_conflicts=True,
    )


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0033_backfill_teacher_assignment_memberships'),
    ]

    operations = [
        migrations.RunPython(
            backfill_assessment_teacher_subjects,
            migrations.RunPython.noop,
        ),
    ]
