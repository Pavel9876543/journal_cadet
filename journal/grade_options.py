from __future__ import annotations

from django.db.models import Q

from .models import (
    AcademicYear,
    GroupSubject,
    Student,
    StudentSubject,
    StudyGroup,
    Subject,
    Teacher,
)


def _has_inactive_option(*options) -> bool:
    return any(option is not None and not option.is_active for option in options)


def get_grade_groups(
    *,
    student: Student | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
):
    if _has_inactive_option(student, subject, teacher):
        return StudyGroup.objects.none()

    group_assignments = GroupSubject.objects.filter(
        is_active=True,
        group__is_active=True,
        subject__is_active=True,
        teacher__is_active=True,
    )
    individual_assignments = StudentSubject.objects.filter(
        is_active=True,
        student__is_active=True,
        student__group__is_active=True,
        subject__is_active=True,
        teacher__is_active=True,
    )

    if student is not None:
        group_assignments = group_assignments.filter(group_id=student.group_id)
        individual_assignments = individual_assignments.filter(student=student)
    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)
    if academic_year is None:
        group_assignments = group_assignments.filter(group__academic_year__is_active=True)
        individual_assignments = individual_assignments.filter(
            student__group__academic_year__is_active=True,
        )
    else:
        group_assignments = group_assignments.filter(group__academic_year=academic_year)
        individual_assignments = individual_assignments.filter(
            student__group__academic_year=academic_year,
        )

    groups = (
        StudyGroup.objects
        .filter(is_active=True)
        .filter(
            Q(pk__in=group_assignments.values_list('group_id', flat=True))
            | Q(pk__in=individual_assignments.values_list('student__group_id', flat=True))
        )
        .select_related('academic_year')
        .distinct()
        .order_by('academic_year__name', 'name')
    )
    if academic_year is None:
        groups = groups.filter(academic_year__is_active=True)
    else:
        groups = groups.filter(academic_year=academic_year)
    return groups


def get_grade_students(
    *,
    group: StudyGroup | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    base_queryset=None,
):
    if _has_inactive_option(group, subject, teacher):
        return Student.objects.none()

    students = base_queryset if base_queryset is not None else Student.objects.filter(is_active=True)
    students = students.filter(is_active=True, group__is_active=True)

    if group is not None:
        students = students.filter(group=group)
    if academic_year is None:
        students = students.filter(group__academic_year__is_active=True)
    else:
        students = students.filter(group__academic_year=academic_year)

    group_assignment = Q(
        group__group_subjects__is_active=True,
        group__group_subjects__subject__is_active=True,
        group__group_subjects__teacher__is_active=True,
    )
    individual_assignment = Q(
        individual_subjects__is_active=True,
        individual_subjects__subject__is_active=True,
        individual_subjects__teacher__is_active=True,
    )
    if subject is not None:
        group_assignment &= Q(group__group_subjects__subject=subject)
        individual_assignment &= Q(individual_subjects__subject=subject)
    if teacher is not None:
        group_assignment &= Q(group__group_subjects__teacher=teacher)
        individual_assignment &= Q(individual_subjects__teacher=teacher)

    return (
        students
        .filter(group_assignment | individual_assignment)
        .select_related('group', 'group__academic_year', 'instrument')
        .distinct()
        .order_by('full_name')
    )


def get_grade_subjects(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
):
    if _has_inactive_option(group, student, teacher):
        return Subject.objects.none()

    group_assignments = GroupSubject.objects.filter(
        is_active=True,
        group__is_active=True,
        subject__is_active=True,
        teacher__is_active=True,
    )
    individual_assignments = StudentSubject.objects.filter(
        is_active=True,
        student__is_active=True,
        student__group__is_active=True,
        subject__is_active=True,
        teacher__is_active=True,
    )

    if group is not None:
        group_assignments = group_assignments.filter(group=group)
        individual_assignments = individual_assignments.filter(student__group=group)
    if student is not None:
        group_assignments = group_assignments.filter(group_id=student.group_id)
        individual_assignments = individual_assignments.filter(student=student)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)
    if academic_year is None:
        group_assignments = group_assignments.filter(group__academic_year__is_active=True)
        individual_assignments = individual_assignments.filter(
            student__group__academic_year__is_active=True,
        )
    else:
        group_assignments = group_assignments.filter(group__academic_year=academic_year)
        individual_assignments = individual_assignments.filter(
            student__group__academic_year=academic_year,
        )

    return (
        Subject.objects
        .filter(is_active=True)
        .filter(
            Q(pk__in=group_assignments.values_list('subject_id', flat=True))
            | Q(pk__in=individual_assignments.values_list('subject_id', flat=True))
        )
        .distinct()
        .order_by('name')
    )


def get_grade_teachers(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    subject: Subject | None = None,
    academic_year: AcademicYear | None = None,
):
    if _has_inactive_option(group, student, subject):
        return Teacher.objects.none()

    group_assignments = GroupSubject.objects.filter(
        is_active=True,
        group__is_active=True,
        subject__is_active=True,
        teacher__is_active=True,
    )
    individual_assignments = StudentSubject.objects.filter(
        is_active=True,
        student__is_active=True,
        student__group__is_active=True,
        subject__is_active=True,
        teacher__is_active=True,
    )

    if group is not None:
        group_assignments = group_assignments.filter(group=group)
        individual_assignments = individual_assignments.filter(student__group=group)
    if student is not None:
        group_assignments = group_assignments.filter(group_id=student.group_id)
        individual_assignments = individual_assignments.filter(student=student)
    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)
    if academic_year is None:
        group_assignments = group_assignments.filter(group__academic_year__is_active=True)
        individual_assignments = individual_assignments.filter(
            student__group__academic_year__is_active=True,
        )
    else:
        group_assignments = group_assignments.filter(group__academic_year=academic_year)
        individual_assignments = individual_assignments.filter(
            student__group__academic_year=academic_year,
        )

    return (
        Teacher.objects
        .filter(is_active=True)
        .filter(
            Q(pk__in=group_assignments.values_list('teacher_id', flat=True))
            | Q(pk__in=individual_assignments.values_list('teacher_id', flat=True))
        )
        .distinct()
        .order_by('full_name')
    )
