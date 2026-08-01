from collections import defaultdict

from django.db import migrations, models
import django.db.models.deletion


def canonicalize_journal_assignments(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    AssessmentItem = apps.get_model('journal', 'AssessmentItem')
    GroupSubject = apps.get_model('journal', 'GroupSubject')
    Student = apps.get_model('journal', 'Student')
    StudentAssessmentGroup = apps.get_model('journal', 'StudentAssessmentGroup')
    StudentEnrollment = apps.get_model('journal', 'StudentEnrollment')
    StudentSubject = apps.get_model('journal', 'StudentSubject')
    Teacher = apps.get_model('journal', 'Teacher')
    TeacherEnrollment = apps.get_model('journal', 'TeacherEnrollment')
    TeacherSubject = apps.get_model('journal', 'TeacherSubject')
    UserAcademicYearMembership = apps.get_model('journal', 'UserAcademicYearMembership')

    def sync_teacher_helpers(teacher_id, year_id, subject_id, is_active):
        if not teacher_id or not year_id or not subject_id:
            return
        TeacherSubject.objects.get_or_create(
            teacher_id=teacher_id,
            subject_id=subject_id,
        )
        membership, _ = TeacherEnrollment.objects.get_or_create(
            teacher_id=teacher_id,
            academic_year_id=year_id,
            defaults={'is_active': is_active},
        )
        if is_active and not membership.is_active:
            TeacherEnrollment.objects.filter(pk=membership.pk).update(is_active=True)
        user_id = Teacher.objects.filter(pk=teacher_id).values_list(
            'user_id', flat=True
        ).first()
        if user_id:
            user_membership, _ = UserAcademicYearMembership.objects.get_or_create(
                user_id=user_id,
                academic_year_id=year_id,
                defaults={'is_active': is_active},
            )
            if is_active and not user_membership.is_active:
                UserAcademicYearMembership.objects.filter(
                    pk=user_membership.pk,
                ).update(is_active=True)

    # GroupSubject/StudentSubject/AssessmentItem are the real teacher
    # assignments.  Helper rows are synchronized from them, never the reverse.
    for assignment in GroupSubject.objects.select_related('group').iterator():
        sync_teacher_helpers(
            assignment.teacher_id,
            assignment.group.academic_year_id,
            assignment.subject_id,
            assignment.is_active,
        )
    for assignment in StudentSubject.objects.iterator():
        sync_teacher_helpers(
            assignment.teacher_id,
            assignment.academic_year_id,
            assignment.subject_id,
            assignment.is_active,
        )

    # AssessmentGroup is the only owner of subject/year for a work. Repair
    # legacy snapshots without using AssessmentResult as an assignment source.
    for item in AssessmentItem.objects.select_related('group').iterator():
        updates = {}
        if item.subject_id != item.group.subject_id:
            updates['subject_id'] = item.group.subject_id
        if item.academic_year_id != item.group.academic_year_id:
            updates['academic_year_id'] = item.group.academic_year_id
        if updates:
            AssessmentItem.objects.filter(pk=item.pk).update(**updates)
        if item.responsible_teacher_id:
            sync_teacher_helpers(
                item.responsible_teacher_id,
                item.group.academic_year_id,
                item.group.subject_id,
                item.is_active,
            )

    # One student/group pair can only belong to the group's year. Merge old
    # duplicates before replacing the legacy three-column unique constraint.
    grouped = defaultdict(list)
    for row in StudentAssessmentGroup.objects.select_related(
        'assessment_group'
    ).order_by('pk').iterator():
        grouped[(row.student_id, row.assessment_group_id)].append(row)

    for (student_id, _group_id), rows in grouped.items():
        canonical_year_id = rows[0].assessment_group.academic_year_id
        survivor = next(
            (row for row in rows if row.academic_year_id == canonical_year_id),
            rows[0],
        )
        duplicate_ids = [row.pk for row in rows if row.pk != survivor.pk]
        if duplicate_ids:
            StudentAssessmentGroup.objects.filter(pk__in=duplicate_ids).delete()
        enrollment_id = StudentEnrollment.objects.filter(
            student_id=student_id,
            academic_year_id=canonical_year_id,
        ).values_list('pk', flat=True).first()
        StudentAssessmentGroup.objects.filter(pk=survivor.pk).update(
            academic_year_id=canonical_year_id,
            enrollment_id=enrollment_id,
            is_active=any(row.is_active for row in rows),
        )

    # Current Student/Teacher profile flags are mirrors of the active year's
    # canonical records. They are not used to grant journal access.
    active_year_id = AcademicYear.objects.filter(is_active=True).values_list(
        'pk', flat=True
    ).first()
    if active_year_id:
        active_enrollments = StudentEnrollment.objects.filter(
            academic_year_id=active_year_id
        )
        Student.objects.exclude(
            pk__in=active_enrollments.values_list('student_id', flat=True)
        ).update(group_id=None, is_active=False)
        for enrollment in active_enrollments.iterator():
            Student.objects.filter(pk=enrollment.student_id).update(
                group_id=enrollment.group_id,
                is_active=enrollment.is_active,
            )

        group_teacher_ids = GroupSubject.objects.filter(
            group__academic_year_id=active_year_id,
            group__is_active=True,
            subject__is_active=True,
            is_active=True,
        ).values_list('teacher_id', flat=True)
        individual_teacher_ids = StudentSubject.objects.filter(
            academic_year_id=active_year_id,
            subject__is_active=True,
            is_active=True,
        ).values_list('teacher_id', flat=True)
        assessment_teacher_ids = AssessmentItem.objects.filter(
            group__academic_year_id=active_year_id,
            group__is_active=True,
            group__subject__is_active=True,
            is_active=True,
            responsible_teacher__isnull=False,
        ).values_list('responsible_teacher_id', flat=True)
        assigned_teacher_ids = Teacher.objects.filter(
            models.Q(pk__in=group_teacher_ids)
            | models.Q(pk__in=individual_teacher_ids)
            | models.Q(pk__in=assessment_teacher_ids)
        ).values_list('pk', flat=True)
        Teacher.objects.exclude(pk__in=assigned_teacher_ids).update(is_active=False)
        Teacher.objects.filter(pk__in=assigned_teacher_ids).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0037_repair_journal_relation_integrity'),
    ]

    operations = [
        migrations.RunPython(
            canonicalize_journal_assignments,
            migrations.RunPython.noop,
        ),
        migrations.RemoveConstraint(
            model_name='studentassessmentgroup',
            name='unique_student_assessment_group_year',
        ),
        migrations.AddConstraint(
            model_name='studentassessmentgroup',
            constraint=models.UniqueConstraint(
                fields=('student', 'assessment_group'),
                name='unique_student_assessment_group',
            ),
        ),
        migrations.AlterField(
            model_name='assessmentitem',
            name='subject',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='assessment_items',
                to='journal.subject',
                verbose_name='Предмет',
            ),
        ),
        migrations.AlterField(
            model_name='assessmentitem',
            name='academic_year',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='assessment_items',
                to='journal.academicyear',
                verbose_name='Учебный год',
            ),
        ),
        migrations.AlterField(
            model_name='studentassessmentgroup',
            name='academic_year',
            field=models.ForeignKey(
                blank=True,
                editable=False,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='student_assessment_group_assignments',
                to='journal.academicyear',
                verbose_name='Учебный год',
            ),
        ),
    ]
