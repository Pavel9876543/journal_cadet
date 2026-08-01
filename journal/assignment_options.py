from __future__ import annotations

from django.db.models import QuerySet

from .models import AcademicYear, Student, StudyGroup, Subject, Teacher


def group_subject_queryset() -> QuerySet[Subject]:
    """Subjects that may be assigned to a whole group.

    Element-assessment subjects are configured through AssessmentGroup and
    AssessmentItem, never through GroupSubject. Keeping the two assignment
    systems separate prevents a teacher from receiving contradictory scopes.
    """
    return Subject.objects.filter(
        is_active=True,
        is_specialty=False,
        assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
    ).order_by('name', 'pk')


def student_subject_queryset() -> QuerySet[Subject]:
    """Standard individual subjects available for StudentSubject."""
    return Subject.objects.filter(
        is_active=True,
        is_specialty=True,
        assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
    ).order_by('name', 'pk')


def active_group_queryset() -> QuerySet[StudyGroup]:
    return StudyGroup.objects.filter(
        is_active=True,
        academic_year__is_active=True,
    ).select_related('academic_year').order_by(
        'academic_year__name',
        'name',
        'pk',
    )


def active_student_queryset() -> QuerySet[Student]:
    """Students participating in the active year.

    Student.is_active is only a current-profile mirror. StudentEnrollment is
    the authoritative year-specific participation record.
    """
    active_year = AcademicYear.get_active()
    if active_year is None:
        return Student.objects.none()
    return Student.objects.filter(
        enrollments__academic_year=active_year,
        enrollments__is_active=True,
    ).select_related('group', 'group__academic_year').distinct().order_by(
        'full_name',
        'pk',
    )


def assignment_teacher_queryset(
    subject: Subject | None = None,
    academic_year: AcademicYear | None = None,
) -> QuerySet[Teacher]:
    """Teachers that may receive a new assignment.

    TeacherEnrollment and TeacherSubject are derived helper records. They must
    neither grant nor block the creation of GroupSubject, StudentSubject or an
    AssessmentItem. The assignment itself is the source of truth and its save
    hook synchronizes those helper rows.
    """
    return Teacher.objects.filter(user__is_active=True).select_related('user').order_by(
        'full_name',
        'pk',
    )
