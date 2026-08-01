from __future__ import annotations

from .access_scope import JournalAccessScope, teacher_assignment_years
from .models import AcademicYear, Teacher


def teacher_assignment_year_ids(teacher: Teacher | None):
    if teacher is None or not getattr(teacher, 'pk', None):
        return AcademicYear.objects.none().values_list('pk', flat=True)
    return teacher_assignment_years(teacher).values_list('pk', flat=True)


def teacher_has_active_assignment(
    teacher: Teacher | None,
    academic_year: AcademicYear | None,
) -> bool:
    if (
        teacher is None
        or academic_year is None
        or not getattr(teacher, 'pk', None)
        or not academic_year.is_active
    ):
        return False
    scope = JournalAccessScope(academic_year, teacher=teacher)
    return bool(
        scope.group_subjects().exists()
        or scope.student_subjects().exists()
        or scope.assessment_items().exists()
    )
