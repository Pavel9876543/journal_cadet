from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Q

from .models import (
    AcademicYear,
    AssessmentGroup,
    AssessmentItem,
    Student,
    StudentEnrollment,
    StudyGroup,
    Subject,
    Teacher,
)


@dataclass(frozen=True)
class AssessmentFilterSelection:
    academic_year: AcademicYear
    teacher: Teacher | None = None
    subject: Subject | None = None
    study_group: StudyGroup | None = None
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
    def study_group_id(self):
        return getattr(self.study_group, 'pk', None)

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
    changed_field = params.get('changed') or ''
    teacher = fixed_teacher or _selected(
        Teacher.objects.filter(is_active=True),
        params.get('assessment_teacher'),
    )
    subject = _selected(
        Subject.objects.filter(assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS),
        params.get('assessment_subject'),
    )
    study_group = _selected(
        StudyGroup.objects.filter(academic_year=academic_year),
        params.get('assessment_study_group'),
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

    if item is not None and changed_field in {'', 'assessment_item'}:
        assessment_group = item.group
        subject = item.subject
    elif assessment_group is not None and changed_field in {'', 'assessment_group'}:
        subject = assessment_group.subject

    return AssessmentFilterSelection(
        academic_year=academic_year,
        teacher=teacher,
        subject=subject,
        study_group=study_group,
        assessment_group=assessment_group,
        item=item,
        student=student,
    )


def _items_for_selection(
    selection: AssessmentFilterSelection,
    *,
    omit: str = '',
):
    items = AssessmentItem.objects.filter(
        academic_year=selection.academic_year,
        subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
        responsible_teacher__isnull=False,
    ).select_related('subject', 'academic_year', 'group', 'responsible_teacher')
    if selection.academic_year.is_active:
        items = items.filter(is_active=True, group__is_active=True)
    if omit != 'teacher' and selection.teacher is not None:
        items = items.filter(responsible_teacher=selection.teacher)
    if omit != 'subject' and selection.subject is not None:
        items = items.filter(subject=selection.subject)
    if omit != 'assessment_group' and selection.assessment_group is not None:
        items = items.filter(group=selection.assessment_group)
    if omit != 'item' and selection.item is not None:
        items = items.filter(pk=selection.item.pk)
    if omit != 'study_group' and selection.study_group is not None:
        items = items.filter(
            group__student_assignments__academic_year=selection.academic_year,
            group__student_assignments__is_active=True,
            group__student_assignments__student__enrollments__academic_year=selection.academic_year,
            group__student_assignments__student__enrollments__group=selection.study_group,
        )
    if omit != 'student' and selection.student is not None:
        items = items.filter(
            group__student_assignments__academic_year=selection.academic_year,
            group__student_assignments__is_active=True,
            group__student_assignments__student=selection.student,
        )
    return items.distinct()


def assessment_filter_querysets(
    selection: AssessmentFilterSelection,
    *,
    allowed_academic_years,
    fixed_teacher: Teacher | None = None,
) -> dict:
    teacher_items = _items_for_selection(selection, omit='teacher')
    subject_items = _items_for_selection(selection, omit='subject')
    group_items = _items_for_selection(selection, omit='study_group')
    assessment_group_items = _items_for_selection(selection, omit='assessment_group')
    item_items = _items_for_selection(selection, omit='item')
    student_items = _items_for_selection(selection, omit='student')

    teachers = Teacher.objects.filter(
        pk__in=teacher_items.exclude(responsible_teacher=None).values('responsible_teacher_id'),
    ).order_by('full_name')
    if fixed_teacher is not None:
        teachers = teachers.filter(pk=fixed_teacher.pk)

    subjects = Subject.objects.filter(
        pk__in=subject_items.values('subject_id'),
    ).order_by('name')

    study_group_enrollments = _eligible_enrollments_for_items(
        selection,
        group_items,
        omit='study_group',
    )
    study_groups = StudyGroup.objects.filter(
        pk__in=study_group_enrollments.exclude(group=None).values('group_id'),
    ).select_related('academic_year').order_by('name')

    assessment_groups = AssessmentGroup.objects.filter(
        pk__in=assessment_group_items.values('group_id'),
    ).select_related('subject', 'academic_year').distinct().order_by(
        'subject__name',
        'sort_order',
        'name',
    )
    items = item_items.order_by(
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
            student_items,
            omit='student',
        ).values('student_id'),
    ).order_by('full_name', 'pk')

    return {
        'academic_years': allowed_academic_years.order_by('-starts_on', '-pk'),
        'teachers': teachers,
        'subjects': subjects,
        'study_groups': study_groups,
        'assessment_groups': assessment_groups,
        'items': items,
        'students': students,
    }


def _eligible_enrollments_for_items(
    selection: AssessmentFilterSelection,
    items,
    *,
    omit: str,
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
    if omit != 'study_group' and selection.study_group is not None:
        enrollments = enrollments.filter(group=selection.study_group)
    if omit != 'student' and selection.student is not None:
        enrollments = enrollments.filter(student=selection.student)
    return enrollments.select_related('student', 'group', 'academic_year').distinct()


def serialize_assessment_filter_options(options: dict) -> dict:
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
            for item in options['subjects']
        ],
        'study_groups': [
            {'id': item.pk, 'label': item.name}
            for item in options['study_groups']
        ],
        'assessment_groups': [
            {
                'id': item.pk,
                'label': item.name,
                'subject_id': item.subject_id,
            }
            for item in options['assessment_groups']
        ],
        'items': [
            {
                'id': item.pk,
                'label': item.title,
                'subject_id': item.subject_id,
                'assessment_group_id': item.group_id,
            }
            for item in options['items']
        ],
        'students': [
            {'id': item.pk, 'label': item.full_name}
            for item in options['students']
        ],
    }
