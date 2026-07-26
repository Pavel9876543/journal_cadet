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
