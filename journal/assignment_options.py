from __future__ import annotations

from django.db.models import Exists, OuterRef, Q, QuerySet

from .models import AcademicYear, Student, StudyGroup, Subject, Teacher, TeacherEnrollment


def is_default_specialty_assignment(subject: Subject | None) -> bool:
    if subject is None:
        return False
    normalized_name = (subject.name or '').strip().lower().replace('ё', 'е')
    return normalized_name == 'специальность' or normalized_name.startswith('специальность ')


def group_subject_queryset() -> QuerySet[Subject]:
    return Subject.objects.filter(is_active=True, is_specialty=False).order_by('name')


def student_subject_queryset() -> QuerySet[Subject]:
    return Subject.objects.filter(is_active=True, is_specialty=True).order_by('name')


def active_group_queryset() -> QuerySet[StudyGroup]:
    return StudyGroup.objects.filter(is_active=True, academic_year__is_active=True).select_related('academic_year').order_by(
        'academic_year__name',
        'name',
    )


def active_student_queryset() -> QuerySet[Student]:
    return Student.objects.filter(is_active=True).filter(
        Q(group__isnull=True) | Q(group__academic_year__is_active=True),
    ).select_related('group', 'group__academic_year').order_by(
        'full_name',
        'pk',
    )


def assignment_teacher_queryset(subject: Subject | None = None) -> QuerySet[Teacher]:
    # A historical teacher may be enrolled into the new active year by making
    # an assignment. A teacher explicitly deactivated in the current year must
    # stay unavailable until reactivated deliberately.
    active_year = AcademicYear.get_active()
    if active_year is None:
        return Teacher.objects.filter(is_active=True).order_by('full_name')

    current_membership = TeacherEnrollment.objects.filter(
        teacher_id=OuterRef('pk'),
        academic_year=active_year,
    )
    return (
        Teacher.objects
        .annotate(_has_current_membership=Exists(current_membership))
        .filter(Q(is_active=True) | Q(_has_current_membership=False))
        .order_by('full_name')
    )
