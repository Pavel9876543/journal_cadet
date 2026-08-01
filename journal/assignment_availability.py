from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Q

from .access_scope import JournalAccessScope
from .models import (
    AcademicYear,
    Student,
    StudyGroup,
    Subject,
    Teacher,
)


def _selected_year(academic_year: AcademicYear | None) -> AcademicYear | None:
    return academic_year or AcademicYear.get_active()


def _normalized_modes(assessment_modes: Iterable[str] | str | None) -> tuple[str, ...] | None:
    if assessment_modes is None:
        return None
    if isinstance(assessment_modes, str):
        return (assessment_modes,)
    return tuple(assessment_modes)


def _is_standard_only(assessment_modes) -> bool:
    modes = _normalized_modes(assessment_modes)
    return modes in (None, (Subject.ASSESSMENT_MODE_STANDARD,))


def available_groups(
    *,
    student: Student | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
):
    year = _selected_year(academic_year)
    if year is None or not _is_standard_only(assessment_modes):
        return StudyGroup.objects.none()
    scope = JournalAccessScope(year, teacher=teacher)
    groups = scope.standard_groups()
    if subject is not None:
        group_ids = scope.group_subjects().filter(subject=subject).values_list('group_id', flat=True)
        individual_student_ids = scope.student_subjects().filter(subject=subject).values_list(
            'student_id', flat=True
        )
        individual_group_ids = scope.standard_enrollments(subject=subject).exclude(
            group_id=None
        ).values_list('group_id', flat=True)
        groups = groups.filter(Q(pk__in=group_ids) | Q(pk__in=individual_group_ids))
    if student is not None:
        enrollment = student.enrollment_for_year(year)
        if enrollment is None:
            return StudyGroup.objects.none()
        student_has_individual = scope.student_subjects().filter(student=student)
        if subject is not None:
            student_has_individual = student_has_individual.filter(subject=subject)
        allowed = Q(pk=enrollment.group_id) if enrollment.group_id else Q(pk__in=[])
        if student_has_individual.exists() and enrollment.group_id:
            allowed |= Q(pk=enrollment.group_id)
        groups = groups.filter(allowed)
    return groups.distinct().order_by('name', 'pk')


def available_students(
    *,
    group: StudyGroup | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
    base_queryset=None,
    individual_only: bool = False,
):
    year = _selected_year(academic_year)
    if year is None or not _is_standard_only(assessment_modes):
        return Student.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Student.objects.none()

    scope = JournalAccessScope(year, teacher=teacher)
    if individual_only:
        assignments = scope.student_subjects()
        if group is not None:
            group_student_ids = scope.standard_enrollments(group=group).values_list('student_id', flat=True)
            assignments = assignments.filter(student_id__in=group_student_ids)
        if subject is not None:
            assignments = assignments.filter(subject=subject)
        student_ids = assignments.values_list('student_id', flat=True)
    else:
        student_ids = scope.standard_enrollments(group=group, subject=subject).values_list(
            'student_id', flat=True
        )
    queryset = base_queryset.prefetch_related(None) if base_queryset is not None else Student.objects.all()
    return queryset.filter(pk__in=student_ids).distinct().order_by('full_name', 'pk')


def available_subjects(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
    individual_only: bool = False,
):
    year = _selected_year(academic_year)
    if year is None or not _is_standard_only(assessment_modes):
        return Subject.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Subject.objects.none()
    scope = JournalAccessScope(year, teacher=teacher)
    if individual_only:
        assignments = scope.student_subjects()
        if student is not None:
            assignments = assignments.filter(student=student)
        if group is not None:
            student_ids = scope.standard_enrollments(group=group).values_list('student_id', flat=True)
            assignments = assignments.filter(student_id__in=student_ids)
        return Subject.objects.filter(
            pk__in=assignments.values_list('subject_id', flat=True)
        ).distinct().order_by('name', 'pk')
    return scope.standard_subjects(group=group, student=student)


def available_teachers(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    subject: Subject | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
    individual_only: bool = False,
):
    year = _selected_year(academic_year)
    if year is None or not _is_standard_only(assessment_modes):
        return Teacher.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Teacher.objects.none()
    scope = JournalAccessScope(year)
    if individual_only:
        assignments = scope.student_subjects()
        if student is not None:
            assignments = assignments.filter(student=student)
        if group is not None:
            student_ids = scope.standard_enrollments(group=group).values_list('student_id', flat=True)
            assignments = assignments.filter(student_id__in=student_ids)
        if subject is not None:
            assignments = assignments.filter(subject=subject)
        return Teacher.objects.filter(
            pk__in=assignments.values_list('teacher_id', flat=True)
        ).distinct().order_by('full_name', 'pk')
    return scope.standard_teachers(group=group, student=student, subject=subject)
