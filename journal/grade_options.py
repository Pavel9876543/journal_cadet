from __future__ import annotations

from .assignment_availability import (
    available_groups,
    available_students,
    available_subjects,
    available_teachers,
)
from .models import Subject


GRADE_ASSESSMENT_MODES = (Subject.ASSESSMENT_MODE_STANDARD,)


def get_grade_groups(**kwargs):
    return available_groups(assessment_modes=GRADE_ASSESSMENT_MODES, **kwargs)


def get_grade_students(**kwargs):
    return available_students(assessment_modes=GRADE_ASSESSMENT_MODES, **kwargs)


def get_grade_subjects(**kwargs):
    return available_subjects(assessment_modes=GRADE_ASSESSMENT_MODES, **kwargs)


def get_grade_teachers(**kwargs):
    return available_teachers(assessment_modes=GRADE_ASSESSMENT_MODES, **kwargs)


def get_grade_form_options(
    *,
    academic_year,
    group=None,
    fixed_teacher=None,
    teacher=None,
    student=None,
    subject=None,
    students_queryset=None,
    individual_only=False,
):
    """Return mutually compatible options for the grade form.

    Groups remain an optional year-level filter. Students are narrowed by the
    group, subject and teacher when those values are selected. Subjects are
    narrowed by the group, student and teacher. This also keeps individual
    assignments usable when the student has no study group.
    """
    effective_teacher = fixed_teacher or teacher
    groups = get_grade_groups(
        teacher=fixed_teacher,
        academic_year=academic_year,
    )
    if academic_year is None:
        students = get_grade_students().none()
        subjects = get_grade_subjects().none()
        teachers = get_grade_teachers().none()
    elif group is not None and group.academic_year_id != academic_year.pk:
        students = get_grade_students().none()
        subjects = get_grade_subjects().none()
        teachers = get_grade_teachers().none()
    else:
        students = get_grade_students(
            group=group,
            subject=subject,
            teacher=effective_teacher,
            academic_year=academic_year,
            base_queryset=students_queryset,
            individual_only=individual_only,
        )
        subjects = get_grade_subjects(
            group=group,
            student=student,
            teacher=effective_teacher,
            academic_year=academic_year,
            individual_only=individual_only,
        )
        teachers = get_grade_teachers(
            group=group,
            student=student,
            subject=subject,
            academic_year=academic_year,
            individual_only=individual_only,
        )
        if fixed_teacher is not None:
            teachers = teachers.filter(pk=fixed_teacher.pk)

    return {
        'groups': groups,
        'students': students,
        'subjects': subjects,
        'teachers': teachers,
    }
