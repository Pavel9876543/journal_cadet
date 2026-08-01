from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from .assessment_services import assessment_items_visible_to_teacher
from .models import (
    AcademicYear,
    AssessmentGroup,
    AssessmentItem,
    Student,
    StudentEnrollment,
    Subject,
    Teacher,
)


@dataclass(frozen=True)
class AssessmentFilterSelection:
    academic_year: AcademicYear
    teacher: Teacher | None = None
    subject: Subject | None = None
    assessment_group: AssessmentGroup | None = None
    item: AssessmentItem | None = None
    student: Student | None = None

    @property
    def academic_year_id(self):
        return self.academic_year.pk

    @property
    def teacher_id(self):
        return getattr(self.teacher, 'pk', None)

    @property
    def subject_id(self):
        return getattr(self.subject, 'pk', None)

    @property
    def assessment_group_id(self):
        return getattr(self.assessment_group, 'pk', None)

    @property
    def item_id(self):
        return getattr(self.item, 'pk', None)

    @property
    def student_id(self):
        return getattr(self.student, 'pk', None)


def _selected(queryset, raw_pk):
    if not raw_pk:
        return None
    try:
        return queryset.filter(pk=raw_pk).first()
    except (TypeError, ValueError):
        return None


def resolve_assessment_filter_selection(
    params,
    *,
    academic_year: AcademicYear,
    fixed_teacher: Teacher | None = None,
) -> AssessmentFilterSelection:
    teacher = fixed_teacher or _selected(
        Teacher.objects.filter(is_active=True),
        params.get('assessment_teacher'),
    )
    subject = _selected(
        Subject.objects.filter(assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS),
        params.get('assessment_subject'),
    )
    assessment_group = _selected(
        AssessmentGroup.objects.filter(academic_year=academic_year),
        params.get('assessment_group'),
    )
    item = _selected(
        AssessmentItem.objects.filter(academic_year=academic_year).select_related('group', 'subject'),
        params.get('assessment_item'),
    )
    student = _selected(
        Student.objects.all(),
        params.get('assessment_student'),
    )

    return AssessmentFilterSelection(
        academic_year=academic_year,
        teacher=teacher,
        subject=subject,
        assessment_group=assessment_group,
        item=item,
        student=student,
    )


def _available_items(
    selection: AssessmentFilterSelection,
    *,
    fixed_teacher: Teacher | None = None,
):
    if fixed_teacher is not None:
        return assessment_items_visible_to_teacher(
            fixed_teacher,
            selection.academic_year,
        )

    items = AssessmentItem.objects.filter(
        academic_year=selection.academic_year,
        subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
        responsible_teacher__isnull=False,
    ).select_related('subject', 'academic_year', 'group', 'responsible_teacher')
    if selection.academic_year.is_active:
        items = items.filter(is_active=True, group__is_active=True)
    return items.distinct()


def assessment_filter_querysets(
    selection: AssessmentFilterSelection,
    *,
    allowed_academic_years,
    fixed_teacher: Teacher | None = None,
) -> dict:
    all_items = _available_items(selection, fixed_teacher=fixed_teacher)
    group_items = all_items
    if selection.assessment_group is not None:
        group_items = group_items.filter(group=selection.assessment_group)

    teachers = Teacher.objects.filter(
        pk__in=group_items.exclude(responsible_teacher=None).values('responsible_teacher_id'),
    ).order_by('full_name')
    if fixed_teacher is not None:
        teachers = teachers.filter(pk=fixed_teacher.pk)

    subjects = Subject.objects.filter(
        pk__in=group_items.values('subject_id'),
    ).order_by('name')

    assessment_groups = AssessmentGroup.objects.filter(
        pk__in=all_items.values('group_id'),
    ).select_related('subject', 'academic_year').distinct().order_by(
        'subject__name',
        'sort_order',
        'name',
    )
    items = group_items.order_by(
        'subject__name',
        'group__sort_order',
        'group__name',
        'sort_order',
        'title',
        'pk',
    )
    students = Student.objects.filter(
        pk__in=_eligible_enrollments_for_items(
            selection,
            group_items,
        ).values('student_id'),
    ).order_by('full_name', 'pk')

    editable_items = group_items
    if fixed_teacher is not None:
        editable_items = editable_items.filter(responsible_teacher=fixed_teacher)
    editable_assessment_groups = AssessmentGroup.objects.filter(
        pk__in=editable_items.values('group_id'),
    ).select_related('subject', 'academic_year').distinct().order_by(
        'subject__name',
        'sort_order',
        'name',
    )
    editable_students = Student.objects.filter(
        pk__in=_eligible_enrollments_for_items(
            selection,
            editable_items,
        ).values('student_id'),
    ).order_by('full_name', 'pk')

    return {
        'academic_years': allowed_academic_years.order_by('-starts_on', '-pk'),
        'teachers': teachers,
        'subjects': subjects,
        'assessment_groups': assessment_groups,
        'items': items,
        'students': students,
        'editable_items': editable_items.order_by(
            'subject__name',
            'group__sort_order',
            'group__name',
            'sort_order',
            'title',
            'pk',
        ),
        'editable_assessment_groups': editable_assessment_groups,
        'editable_students': editable_students,
    }


def _eligible_enrollments_for_items(
    selection: AssessmentFilterSelection,
    items,
):
    assessment_group_ids = items.values('group_id')
    subject_ids = items.values('subject_id')
    enrollments = StudentEnrollment.objects.filter(
        academic_year=selection.academic_year,
        student__assessment_group_assignments__academic_year=selection.academic_year,
        student__assessment_group_assignments__assessment_group_id__in=assessment_group_ids,
        student__assessment_group_assignments__is_active=True,
    ).filter(
        Q(
            group__group_subjects__subject_id__in=subject_ids,
            group__group_subjects__is_active=True,
        )
        | Q(
            student__individual_subjects__subject_id__in=subject_ids,
            student__individual_subjects__academic_year=selection.academic_year,
            student__individual_subjects__is_active=True,
        )
    )
    if selection.academic_year.is_active:
        enrollments = enrollments.filter(is_active=True, student__is_active=True)
    return enrollments.select_related('student', 'group', 'academic_year').distinct()


def serialize_assessment_filter_options(
    options: dict,
    *,
    editable_only: bool = False,
) -> dict:
    items = options['editable_items'] if editable_only else options['items']
    assessment_groups = (
        options['editable_assessment_groups']
        if editable_only
        else options['assessment_groups']
    )
    students = options['editable_students'] if editable_only else options['students']
    subjects = Subject.objects.filter(
        pk__in=items.values('subject_id'),
    ).order_by('name')
    return {
        'academic_years': [
            {'id': item.pk, 'label': item.name}
            for item in options['academic_years']
        ],
        'teachers': [
            {'id': item.pk, 'label': item.full_name}
            for item in options['teachers']
        ],
        'subjects': [
            {'id': item.pk, 'label': item.name}
            for item in subjects
        ],
        'assessment_groups': [
            {
                'id': item.pk,
                'label': item.name,
                'subject_id': item.subject_id,
            }
            for item in assessment_groups
        ],
        'items': [
            {
                'id': item.pk,
                'label': item.title,
                'subject_id': item.subject_id,
                'assessment_group_id': item.group_id,
            }
            for item in items
        ],
        'students': [
            {'id': item.pk, 'label': item.full_name}
            for item in students
        ],
    }
