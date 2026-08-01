from __future__ import annotations

from django.db.models import Q, QuerySet

from .models import AcademicYear, AssessmentItem, GroupSubject, StudentSubject, Teacher


def teacher_assignment_year_ids(teacher: Teacher | None) -> QuerySet:
    """Return academic years that contain a real assignment for ``teacher``.

    TeacherEnrollment is useful as a participation record, but the actual
    access source is an assignment: a group subject, an individual subject or
    an assessment item where the teacher is responsible.  Existing databases
    may contain assignments created before participation rows were introduced,
    so the personal cabinet must not become empty merely because that helper
    row is missing.
    """
    if teacher is None or not getattr(teacher, 'pk', None):
        return AcademicYear.objects.none().values_list('pk', flat=True)

    group_year_ids = GroupSubject.objects.filter(
        teacher=teacher,
    ).values('group__academic_year_id')
    individual_year_ids = StudentSubject.objects.filter(
        teacher=teacher,
    ).values('academic_year_id')
    assessment_year_ids = AssessmentItem.objects.filter(
        responsible_teacher=teacher,
    ).values('academic_year_id')

    return (
        AcademicYear.objects
        .filter(
            Q(pk__in=group_year_ids)
            | Q(pk__in=individual_year_ids)
            | Q(pk__in=assessment_year_ids)
        )
        .values_list('pk', flat=True)
    )


def teacher_has_active_assignment(
    teacher: Teacher | None,
    academic_year: AcademicYear | None,
) -> bool:
    """Whether the teacher currently owns any editable data in a year."""
    if (
        teacher is None
        or academic_year is None
        or not getattr(teacher, 'pk', None)
        or not academic_year.is_active
    ):
        return False

    return (
        GroupSubject.objects.filter(
            teacher=teacher,
            group__academic_year=academic_year,
            group__is_active=True,
            subject__is_active=True,
            is_active=True,
        ).exists()
        or StudentSubject.objects.filter(
            teacher=teacher,
            academic_year=academic_year,
            student__is_active=True,
            subject__is_active=True,
            is_active=True,
        ).exists()
        or AssessmentItem.objects.filter(
            responsible_teacher=teacher,
            academic_year=academic_year,
            group__is_active=True,
            subject__is_active=True,
            is_active=True,
        ).exists()
    )
