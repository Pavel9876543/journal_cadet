from __future__ import annotations

from dataclasses import dataclass

from .access_scope import JournalAccessScope
from .models import (
    AcademicYear,
    AssessmentGroup,
    AssessmentItem,
    Student,
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
    """Resolve only objects that belong to the selected canonical year.

    ``AssessmentItem.subject`` and ``AssessmentItem.academic_year`` are legacy
    snapshots.  The parent ``AssessmentGroup`` owns both values, therefore all
    selection checks deliberately follow ``group__...`` relations.
    """
    scope = JournalAccessScope(academic_year, teacher=fixed_teacher)
    item_queryset = scope.assessment_items()
    group_queryset = scope.assessment_groups()

    teacher = fixed_teacher or _selected(
        Teacher.objects.filter(
            pk__in=item_queryset.values_list('responsible_teacher_id', flat=True),
        ).distinct(),
        params.get('assessment_teacher'),
    )
    if fixed_teacher is None and teacher is not None:
        item_queryset = item_queryset.filter(responsible_teacher=teacher)
        group_queryset = AssessmentGroup.objects.filter(
            pk__in=item_queryset.values_list('group_id', flat=True)
        )

    subject = _selected(
        Subject.objects.filter(
            assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            assessment_groups__academic_year=academic_year,
        ).distinct(),
        params.get('assessment_subject'),
    )
    assessment_group = _selected(
        group_queryset.filter(academic_year=academic_year),
        params.get('assessment_group'),
    )
    item = _selected(
        item_queryset.filter(group__academic_year=academic_year),
        params.get('assessment_item'),
    )
    student = _selected(
        scope.assessment_students(),
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


def _items_for_selection(
    selection: AssessmentFilterSelection,
    *,
    fixed_teacher: Teacher | None,
):
    scope = JournalAccessScope(selection.academic_year, teacher=fixed_teacher)
    items = scope.assessment_items()
    if fixed_teacher is None and selection.teacher is not None:
        items = items.filter(responsible_teacher=selection.teacher)
    if selection.subject is not None:
        items = items.filter(group__subject=selection.subject)
    if selection.assessment_group is not None:
        items = items.filter(group=selection.assessment_group)
    if selection.item is not None:
        items = items.filter(pk=selection.item.pk)
    if selection.student is not None:
        assigned_group_ids = scope.assessment_assignments().filter(
            student=selection.student,
        ).values_list('assessment_group_id', flat=True)
        items = items.filter(group_id__in=assigned_group_ids)
    return items


def assessment_filter_querysets(
    selection: AssessmentFilterSelection,
    *,
    allowed_academic_years,
    fixed_teacher: Teacher | None = None,
) -> dict:
    """Return one coherent set of options for cabinet filters and editing.

    Every collection is derived from :class:`JournalAccessScope`; saved result
    rows never create visibility and helper participation rows never remove it.
    """
    scope = JournalAccessScope(selection.academic_year, teacher=fixed_teacher)
    scoped_items = scope.assessment_items()
    if fixed_teacher is None and selection.teacher is not None:
        scoped_items = scoped_items.filter(responsible_teacher=selection.teacher)

    option_items = scoped_items
    if selection.subject is not None:
        option_items = option_items.filter(group__subject=selection.subject)
    if selection.assessment_group is not None:
        option_items = option_items.filter(group=selection.assessment_group)
    if selection.student is not None:
        assigned_group_ids = scope.assessment_assignments().filter(
            student=selection.student,
        ).values_list('assessment_group_id', flat=True)
        option_items = option_items.filter(group_id__in=assigned_group_ids)

    displayed_items = _items_for_selection(
        selection,
        fixed_teacher=fixed_teacher,
    )

    assessment_groups = AssessmentGroup.objects.filter(
        pk__in=option_items.values_list('group_id', flat=True),
    ).select_related('subject', 'academic_year').distinct().order_by(
        'subject__name', 'sort_order', 'name', 'pk'
    )
    subjects = Subject.objects.filter(
        pk__in=option_items.values_list('group__subject_id', flat=True),
    ).distinct().order_by('name', 'pk')
    teachers = Teacher.objects.filter(
        pk__in=option_items.exclude(
            responsible_teacher=None,
        ).values_list('responsible_teacher_id', flat=True),
    ).distinct().order_by('full_name', 'pk')
    if fixed_teacher is not None:
        teachers = teachers.filter(pk=fixed_teacher.pk)

    group_ids = option_items.values_list('group_id', flat=True)
    students = scope.assessment_students(group_ids=group_ids)

    # In a teacher cabinet all items in the scope are editable in an active
    # year.  In the admin cabinet all configured items are editable.  Keeping
    # these querysets structurally identical prevents the filter and editor
    # from showing contradictory values.
    editable_items = displayed_items
    editable_group_ids = editable_items.values_list('group_id', flat=True)
    editable_assessment_groups = AssessmentGroup.objects.filter(
        pk__in=editable_group_ids,
    ).select_related('subject', 'academic_year').distinct().order_by(
        'subject__name', 'sort_order', 'name', 'pk'
    )
    editable_students = scope.assessment_students(group_ids=editable_group_ids)

    item_order = (
        'group__subject__name',
        'group__sort_order',
        'group__name',
        'sort_order',
        'title',
        'pk',
    )
    return {
        'academic_years': allowed_academic_years.order_by('-starts_on', '-pk'),
        'teachers': teachers,
        'subjects': subjects,
        'assessment_groups': assessment_groups,
        'items': displayed_items.order_by(*item_order),
        'students': students,
        'editable_items': editable_items.order_by(*item_order),
        'editable_assessment_groups': editable_assessment_groups,
        'editable_students': editable_students,
    }


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
        pk__in=items.values_list('group__subject_id', flat=True),
    ).order_by('name', 'pk')
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
                'subject_id': item.group.subject_id,
                'assessment_group_id': item.group_id,
            }
            for item in items.select_related('group')
        ],
        'students': [
            {'id': item.pk, 'label': item.full_name}
            for item in students
        ],
    }
