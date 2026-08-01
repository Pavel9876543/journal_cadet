from collections import defaultdict

from django.db import migrations
from django.db.models import Q


def _activate_teacher_links(
    *,
    Teacher,
    TeacherEnrollment,
    TeacherSubject,
    UserAcademicYearMembership,
    teacher_id,
    subject_id,
    academic_year_id,
    active,
):
    if not teacher_id or not academic_year_id:
        return
    membership, _created = TeacherEnrollment.objects.get_or_create(
        teacher_id=teacher_id,
        academic_year_id=academic_year_id,
        defaults={'is_active': active},
    )
    if active and not membership.is_active:
        TeacherEnrollment.objects.filter(pk=membership.pk).update(is_active=True)
    if subject_id:
        TeacherSubject.objects.get_or_create(
            teacher_id=teacher_id,
            subject_id=subject_id,
        )
    user_id = Teacher.objects.filter(pk=teacher_id).values_list('user_id', flat=True).first()
    if active:
        Teacher.objects.filter(pk=teacher_id).update(is_active=True)
    if user_id:
        user_membership, _created = UserAcademicYearMembership.objects.get_or_create(
            user_id=user_id,
            academic_year_id=academic_year_id,
            defaults={'is_active': active},
        )
        if active and not user_membership.is_active:
            UserAcademicYearMembership.objects.filter(pk=user_membership.pk).update(is_active=True)


def repair_journal_relation_integrity(apps, schema_editor):
    AcademicYear = apps.get_model('journal', 'AcademicYear')
    AssessmentItem = apps.get_model('journal', 'AssessmentItem')
    AssessmentResult = apps.get_model('journal', 'AssessmentResult')
    GroupSubject = apps.get_model('journal', 'GroupSubject')
    StudentAssessmentGroup = apps.get_model('journal', 'StudentAssessmentGroup')
    StudentEnrollment = apps.get_model('journal', 'StudentEnrollment')
    StudentSubject = apps.get_model('journal', 'StudentSubject')
    Teacher = apps.get_model('journal', 'Teacher')
    TeacherEnrollment = apps.get_model('journal', 'TeacherEnrollment')
    TeacherSubject = apps.get_model('journal', 'TeacherSubject')
    UserAcademicYearMembership = apps.get_model('journal', 'UserAcademicYearMembership')

    active_year_ids = set(
        AcademicYear.objects.filter(is_active=True).values_list('pk', flat=True)
    )

    # Existing result rows prove that a student/item relation existed. Restore
    # a missing or stale StudentAssessmentGroup row so forms and cabinets use
    # the same relation chain as the result changelist.
    result_rows = (
        AssessmentResult.objects
        .select_related('item__group', 'enrollment')
        .order_by('pk')
    )
    for result in result_rows.iterator():
        student_id = result.enrollment.student_id
        group_id = result.item.group_id
        year_id = result.item.academic_year_id
        enrollment_id = result.enrollment_id
        rows = list(
            StudentAssessmentGroup.objects
            .filter(student_id=student_id, assessment_group_id=group_id)
            .order_by('pk')
        )
        survivor = next(
            (row for row in rows if row.academic_year_id == year_id),
            rows[0] if rows else None,
        )
        if survivor is None:
            StudentAssessmentGroup.objects.create(
                student_id=student_id,
                assessment_group_id=group_id,
                academic_year_id=year_id,
                enrollment_id=enrollment_id,
                is_active=(
                    year_id in active_year_ids
                    and result.item.is_active
                    and result.item.group.is_active
                    and result.enrollment.student.is_active
                ),
            )
            continue

        duplicate_ids = [row.pk for row in rows if row.pk != survivor.pk]
        if duplicate_ids:
            StudentAssessmentGroup.objects.filter(pk__in=duplicate_ids).delete()
        should_activate = (
            survivor.is_active
            or (
                year_id in active_year_ids
                and result.item.is_active
                and result.item.group.is_active
                and result.enrollment.student.is_active
            )
        )
        StudentAssessmentGroup.objects.filter(pk=survivor.pk).update(
            academic_year_id=year_id,
            enrollment_id=enrollment_id,
            is_active=should_activate,
        )

    # Synchronize all remaining denormalized assignment fields with the group
    # and the student's enrollment for that same year.
    assignment_groups = defaultdict(list)
    for assignment in (
        StudentAssessmentGroup.objects
        .select_related('assessment_group')
        .order_by('pk')
    ):
        canonical_year_id = assignment.assessment_group.academic_year_id
        assignment_groups[
            (assignment.student_id, assignment.assessment_group_id, canonical_year_id)
        ].append(assignment)

    for (student_id, _group_id, year_id), rows in assignment_groups.items():
        survivor = next(
            (row for row in rows if row.academic_year_id == year_id),
            rows[0],
        )
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
            is_active=any(row.is_active for row in rows),
        )

    # Active relations in an active year are authoritative. Repair enrollment
    # flags that were left inactive by older imports; otherwise the student
    # cabinet can show data while the administrator/teacher cabinet filters it
    # out.
    active_assignment_enrollment_ids = StudentAssessmentGroup.objects.filter(
        is_active=True,
        assessment_group__academic_year_id__in=active_year_ids,
        assessment_group__is_active=True,
        student__is_active=True,
        enrollment__isnull=False,
    ).values('enrollment_id')
    active_result_enrollment_ids = AssessmentResult.objects.filter(
        item__academic_year_id__in=active_year_ids,
        item__is_active=True,
        item__group__is_active=True,
        enrollment__student__is_active=True,
    ).values('enrollment_id')
    active_individual_student_ids = StudentSubject.objects.filter(
        academic_year_id__in=active_year_ids,
        is_active=True,
        student__is_active=True,
    ).values('student_id')
    active_group_ids = GroupSubject.objects.filter(
        group__academic_year_id__in=active_year_ids,
        group__is_active=True,
        is_active=True,
    ).values('group_id')
    StudentEnrollment.objects.filter(
        academic_year_id__in=active_year_ids,
        student__is_active=True,
    ).filter(
        Q(pk__in=active_assignment_enrollment_ids)
        | Q(pk__in=active_result_enrollment_ids)
        | Q(student_id__in=active_individual_student_ids)
        | Q(group_id__in=active_group_ids)
    ).update(is_active=True)

    # Rebuild helper rows from real teaching assignments. These helpers may be
    # absent in upgraded databases, but must never hide assigned data.
    for assignment in GroupSubject.objects.select_related('group').iterator():
        _activate_teacher_links(
            Teacher=Teacher,
            TeacherEnrollment=TeacherEnrollment,
            TeacherSubject=TeacherSubject,
            UserAcademicYearMembership=UserAcademicYearMembership,
            teacher_id=assignment.teacher_id,
            subject_id=assignment.subject_id,
            academic_year_id=assignment.group.academic_year_id,
            active=assignment.is_active,
        )
    for assignment in StudentSubject.objects.iterator():
        _activate_teacher_links(
            Teacher=Teacher,
            TeacherEnrollment=TeacherEnrollment,
            TeacherSubject=TeacherSubject,
            UserAcademicYearMembership=UserAcademicYearMembership,
            teacher_id=assignment.teacher_id,
            subject_id=assignment.subject_id,
            academic_year_id=assignment.academic_year_id,
            active=assignment.is_active,
        )
    for item in AssessmentItem.objects.exclude(responsible_teacher_id=None).iterator():
        _activate_teacher_links(
            Teacher=Teacher,
            TeacherEnrollment=TeacherEnrollment,
            TeacherSubject=TeacherSubject,
            UserAcademicYearMembership=UserAcademicYearMembership,
            teacher_id=item.responsible_teacher_id,
            subject_id=item.subject_id,
            academic_year_id=item.academic_year_id,
            active=item.is_active,
        )

    # A student enrollment is also an authoritative year participation record.
    for enrollment in StudentEnrollment.objects.select_related('student').iterator():
        user_id = enrollment.student.user_id
        if not user_id:
            continue
        membership, _created = UserAcademicYearMembership.objects.get_or_create(
            user_id=user_id,
            academic_year_id=enrollment.academic_year_id,
            defaults={'is_active': enrollment.is_active},
        )
        if enrollment.is_active and not membership.is_active:
            UserAcademicYearMembership.objects.filter(pk=membership.pk).update(is_active=True)


class Migration(migrations.Migration):
    dependencies = [
        ('journal', '0036_repair_assessment_group_assignments'),
    ]

    operations = [
        migrations.RunPython(
            repair_journal_relation_integrity,
            migrations.RunPython.noop,
        ),
    ]
