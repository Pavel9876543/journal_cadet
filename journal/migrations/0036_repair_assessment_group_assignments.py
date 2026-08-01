from collections import defaultdict

from django.db import migrations


def repair_assessment_group_assignments(apps, schema_editor):
    StudentAssessmentGroup = apps.get_model('journal', 'StudentAssessmentGroup')
    StudentEnrollment = apps.get_model('journal', 'StudentEnrollment')

    grouped = defaultdict(list)
    assignments = list(
        StudentAssessmentGroup.objects
        .select_related('assessment_group')
        .order_by('pk')
    )
    for assignment in assignments:
        year_id = assignment.assessment_group.academic_year_id
        key = (assignment.student_id, assignment.assessment_group_id, year_id)
        grouped[key].append(assignment)

    for (student_id, _group_id, year_id), rows in grouped.items():
        # Prefer an already-correct row so fixing an older stale duplicate can
        # never violate the unique constraint before duplicates are removed.
        survivor = next(
            (row for row in rows if row.academic_year_id == year_id),
            rows[0],
        )
        is_active = any(row.is_active for row in rows)
        duplicate_ids = [row.pk for row in rows if row.pk != survivor.pk]
        if duplicate_ids:
            StudentAssessmentGroup.objects.filter(pk__in=duplicate_ids).delete()

        enrollment_id = (
            StudentEnrollment.objects
            .filter(student_id=student_id, academic_year_id=year_id)
            .values_list('pk', flat=True)
            .first()
        )
        StudentAssessmentGroup.objects.filter(pk=survivor.pk).update(
            academic_year_id=year_id,
            enrollment_id=enrollment_id,
            is_active=is_active,
        )


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0035_errorlog_user_message'),
    ]

    operations = [
        migrations.RunPython(
            repair_assessment_group_assignments,
            migrations.RunPython.noop,
        ),
    ]
