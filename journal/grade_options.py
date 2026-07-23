from __future__ import annotations

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
)


def _selected_year(academic_year):
    return academic_year or AcademicYear.get_active()


def _assignment_querysets(academic_year):
    year = _selected_year(academic_year)
    if year is None:
        return year, GroupSubject.objects.none(), StudentSubject.objects.none()

    group_assignments = GroupSubject.objects.filter(
        is_active=True,
        group__academic_year=year,
    )
    individual_assignments = StudentSubject.objects.filter(
        is_active=True,
        academic_year=year,
    )
    if year.is_active:
        group_assignments = group_assignments.filter(
            group__is_active=True,
            subject__is_active=True,
            teacher__is_active=True,
        )
        individual_assignments = individual_assignments.filter(
            student__is_active=True,
            subject__is_active=True,
            teacher__is_active=True,
        )
    return year, group_assignments, individual_assignments


def _student_enrollment(student, academic_year):
    if student is None or academic_year is None:
        return None
    return student.enrollment_for_year(academic_year)


def get_grade_groups(
    *,
    student: Student | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
):
    year, group_assignments, individual_assignments = _assignment_querysets(academic_year)
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

    individual_student_ids = individual_assignments.values_list('student_id', flat=True)
    individual_group_ids = StudentEnrollment.objects.filter(
        academic_year=year,
        student_id__in=individual_student_ids,
    ).values_list('group_id', flat=True)
    groups = StudyGroup.objects.filter(
        academic_year=year,
    ).filter(
        Q(pk__in=group_assignments.values_list('group_id', flat=True))
        | Q(pk__in=individual_group_ids)
    )
    if year.is_active:
        groups = groups.filter(is_active=True)
    return groups.select_related('academic_year').distinct().order_by('name')


def get_grade_students(
    *,
    group: StudyGroup | None = None,
    subject: Subject | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
    base_queryset=None,
):
    year, group_assignments, individual_assignments = _assignment_querysets(academic_year)
    if year is None:
        return Student.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Student.objects.none()

    if group is not None:
        group_assignments = group_assignments.filter(group=group)
        group_enrollments = StudentEnrollment.objects.filter(
            academic_year=year,
            group=group,
        )
        individual_assignments = individual_assignments.filter(
            student_id__in=group_enrollments.values_list('student_id', flat=True),
        )
    else:
        group_enrollments = StudentEnrollment.objects.filter(academic_year=year)

    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    group_student_ids = group_enrollments.filter(
        group_id__in=group_assignments.values_list('group_id', flat=True),
    ).values_list('student_id', flat=True)
    individual_student_ids = individual_assignments.values_list('student_id', flat=True)

    students = (
        base_queryset.prefetch_related(None)
        if base_queryset is not None
        else Student.objects.all()
    )
    students = students.filter(
        Q(pk__in=group_student_ids) | Q(pk__in=individual_student_ids),
    )
    if year.is_active:
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


def get_grade_subjects(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    teacher: Teacher | None = None,
    academic_year: AcademicYear | None = None,
):
    year, group_assignments, individual_assignments = _assignment_querysets(academic_year)
    if year is None:
        return Subject.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Subject.objects.none()

    enrollment = _student_enrollment(student, year)
    if student is not None and enrollment is None:
        return Subject.objects.none()
    selected_group = group or (enrollment.group if enrollment else None)
    if selected_group is not None:
        group_assignments = group_assignments.filter(group=selected_group)
        group_student_ids = StudentEnrollment.objects.filter(
            academic_year=year,
            group=selected_group,
        ).values_list('student_id', flat=True)
        individual_assignments = individual_assignments.filter(student_id__in=group_student_ids)
    if student is not None:
        individual_assignments = individual_assignments.filter(student=student)
    if teacher is not None:
        group_assignments = group_assignments.filter(teacher=teacher)
        individual_assignments = individual_assignments.filter(teacher=teacher)

    subjects = Subject.objects.filter(
        Q(pk__in=group_assignments.values_list('subject_id', flat=True))
        | Q(pk__in=individual_assignments.values_list('subject_id', flat=True))
    )
    if year.is_active:
        subjects = subjects.filter(is_active=True)
    return subjects.distinct().order_by('name')


def get_grade_teachers(
    *,
    group: StudyGroup | None = None,
    student: Student | None = None,
    subject: Subject | None = None,
    academic_year: AcademicYear | None = None,
):
    year, group_assignments, individual_assignments = _assignment_querysets(academic_year)
    if year is None:
        return Teacher.objects.none()
    if group is not None and group.academic_year_id != year.pk:
        return Teacher.objects.none()

    enrollment = _student_enrollment(student, year)
    if student is not None and enrollment is None:
        return Teacher.objects.none()
    selected_group = group or (enrollment.group if enrollment else None)
    if selected_group is not None:
        group_assignments = group_assignments.filter(group=selected_group)
        group_student_ids = StudentEnrollment.objects.filter(
            academic_year=year,
            group=selected_group,
        ).values_list('student_id', flat=True)
        individual_assignments = individual_assignments.filter(student_id__in=group_student_ids)
    if student is not None:
        individual_assignments = individual_assignments.filter(student=student)
    if subject is not None:
        group_assignments = group_assignments.filter(subject=subject)
        individual_assignments = individual_assignments.filter(subject=subject)

    teachers = Teacher.objects.filter(
        Q(pk__in=group_assignments.values_list('teacher_id', flat=True))
        | Q(pk__in=individual_assignments.values_list('teacher_id', flat=True))
    )
    if year.is_active:
        teachers = teachers.filter(is_active=True)
    return teachers.distinct().order_by('full_name')
