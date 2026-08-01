from __future__ import annotations

from collections.abc import Iterable

from django.db.models import Prefetch, Q

from .models import (
    AcademicYear,
    GroupSubject,
    Student,
    StudentEnrollment,
    StudentSubject,
    StudyGroup,
    Subject,
    Teacher,
    academic_year_is_active,
)


def _selected_year(academic_year: AcademicYear | None) -> AcademicYear | None:
    return academic_year or AcademicYear.get_active()


def _normalized_modes(assessment_modes: Iterable[str] | str | None) -> tuple[str, ...] | None:
    if assessment_modes is None:
        return None
    if isinstance(assessment_modes, str):
        return (assessment_modes,)
    return tuple(assessment_modes)


def _assignment_querysets(
    academic_year: AcademicYear | None,
    assessment_modes: Iterable[str] | str | None,
):
    year = _selected_year(academic_year)
    if year is None:
        return year, GroupSubject.objects.none(), StudentSubject.objects.none()

    group_assignments = GroupSubject.objects.filter(group__academic_year=year)
    individual_assignments = StudentSubject.objects.filter(academic_year=year)
    modes = _normalized_modes(assessment_modes)
    if modes is not None:
        group_assignments = group_assignments.filter(subject__assessment_mode__in=modes)
        individual_assignments = individual_assignments.filter(subject__assessment_mode__in=modes)

    if academic_year_is_active(year):
        group_assignments = group_assignments.filter(
            is_active=True,
            group__is_active=True,
            subject__is_active=True,
        )
        individual_assignments = individual_assignments.filter(
            is_active=True,
            student__is_active=True,
            subject__is_active=True,
        )
    return year, group_assignments, individual_assignments


def _student_enrollment(student: Student | None, academic_year: AcademicYear | None):
    if student is None or academic_year is None:
        return None
    return student.enrollment_for_year(academic_year)


def available_groups(
    *,
    student: Student | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
):
    year, group_assignments, individual_assignments = _assignment_querysets(
        academic_year,
        assessment_modes,
    )
    if year is None:
        return StudyGroup.objects.none()

    enrollment = _student_enrollment(student, year)
    if student is not None and enrollment is None:
        return StudyGroup.objects.none()
    if student is not None:
        group_assignments = group_assignments.filter(group_id=enrollment.group_id)
        individual_assignments = individual_assignments.filter(student=student)
    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    individual_group_ids = StudentEnrollment.objects.filter(
        academic_year=year,
        student_id__in=individual_assignments.values('student_id'),
    ).values('group_id')
    groups = StudyGroup.objects.filter(academic_year=year).filter(
        Q(pk__in=group_assignments.values('group_id'))
        | Q(pk__in=individual_group_ids)
    )
    if academic_year_is_active(year):
        groups = groups.filter(is_active=True)
    return groups.select_related('academic_year').distinct().order_by('name')


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
    year, group_assignments, individual_assignments = _assignment_querysets(
        academic_year,
        assessment_modes,
    )
    if year is None:
        return Student.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Student.objects.none()
    if individual_only:
        group_assignments = group_assignments.none()

    group_enrollments = StudentEnrollment.objects.filter(academic_year=year)
    if group is not None:
        group_assignments = group_assignments.filter(group=group)
        group_enrollments = group_enrollments.filter(group=group)
        individual_assignments = individual_assignments.filter(
            student_id__in=group_enrollments.values('student_id'),
        )
    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    group_student_ids = group_enrollments.filter(
        group_id__in=group_assignments.values('group_id'),
    ).values('student_id')
    students = base_queryset.prefetch_related(None) if base_queryset is not None else Student.objects.all()
    students = students.filter(
        Q(pk__in=group_student_ids)
        | Q(pk__in=individual_assignments.values('student_id')),
    )
    if academic_year_is_active(year):
        students = students.filter(is_active=True)

    enrollment_prefetch = Prefetch(
        'enrollments',
        queryset=StudentEnrollment.objects.filter(academic_year=year).select_related(
            'group',
            'academic_year',
        ),
        to_attr='journal_enrollments',
    )
    return (
        students
        .select_related('group', 'group__academic_year', 'instrument')
        .prefetch_related(enrollment_prefetch)
        .distinct()
        .order_by('full_name')
    )


def available_subjects(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
    individual_only: bool = False,
):
    year, group_assignments, individual_assignments = _assignment_querysets(
        academic_year,
        assessment_modes,
    )
    if year is None:
        return Subject.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Subject.objects.none()
    if individual_only:
        group_assignments = group_assignments.none()

    enrollment = _student_enrollment(student, year)
    if student is not None and enrollment is None:
        return Subject.objects.none()
    selected_group = None if individual_only else group or (enrollment.group if enrollment else None)
    if selected_group is not None:
        group_assignments = group_assignments.filter(group=selected_group)
        group_student_ids = StudentEnrollment.objects.filter(
            academic_year=year,
            group=selected_group,
        ).values('student_id')
        individual_assignments = individual_assignments.filter(student_id__in=group_student_ids)
    elif student is not None:
        group_assignments = group_assignments.none()
    if student is not None:
        individual_assignments = individual_assignments.filter(student=student)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    subjects = Subject.objects.filter(
        Q(pk__in=group_assignments.values('subject_id'))
        | Q(pk__in=individual_assignments.values('subject_id'))
    )
    if academic_year_is_active(year):
        subjects = subjects.filter(is_active=True)
    return subjects.distinct().order_by('name')


def available_teachers(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    subject: Subject | None = None,
    academic_year: AcademicYear | None = None,
    assessment_modes: Iterable[str] | str | None = None,
    individual_only: bool = False,
):
    year, group_assignments, individual_assignments = _assignment_querysets(
        academic_year,
        assessment_modes,
    )
    if year is None:
        return Teacher.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Teacher.objects.none()
    if individual_only:
        group_assignments = group_assignments.none()

    enrollment = _student_enrollment(student, year)
    if student is not None and enrollment is None:
        return Teacher.objects.none()
    selected_group = None if individual_only else group or (enrollment.group if enrollment else None)
    if selected_group is not None:
        group_assignments = group_assignments.filter(group=selected_group)
        group_student_ids = StudentEnrollment.objects.filter(
            academic_year=year,
            group=selected_group,
        ).values('student_id')
        individual_assignments = individual_assignments.filter(student_id__in=group_student_ids)
    elif student is not None:
        group_assignments = group_assignments.none()
    if student is not None:
        individual_assignments = individual_assignments.filter(student=student)
    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)

    teachers = Teacher.objects.filter(
        Q(pk__in=group_assignments.values('teacher_id'))
        | Q(pk__in=individual_assignments.values('teacher_id'))
    )
    if academic_year_is_active(year):
        teachers = teachers.filter(is_active=True)
    return teachers.distinct().order_by('full_name')
