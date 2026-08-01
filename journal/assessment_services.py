from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .access_scope import JournalAccessScope
from .models import (
    AcademicYear,
    AssessmentGroup,
    AssessmentItem,
    AssessmentResult,
    FinalGradeRule,
    Student,
    StudentAssessmentGroup,
    StudentEnrollment,
    Subject,
    SubjectResult,
    Teacher,
)


@dataclass(frozen=True)
class FinalGradeCalculation:
    grade: str
    passed_count: int
    total_count: int
    required_count: int
    passed_required_count: int
    not_evaluated_count: int
    all_required_satisfied: bool
    rule_id: int | None
    rule_type: str | None

    def as_details(self) -> dict:
        return {
            'passed_count': self.passed_count,
            'total_count': self.total_count,
            'required_count': self.required_count,
            'passed_required_count': self.passed_required_count,
            'not_evaluated_count': self.not_evaluated_count,
            'all_required_satisfied': self.all_required_satisfied,
            'rule_id': self.rule_id,
            'rule_type': self.rule_type,
        }



def assessment_assignments_for_groups(
    group_ids,
    academic_year: AcademicYear,
    *,
    include_inactive: bool = False,
):
    normalized_group_ids = _normalized_primary_key_values(
        group_ids,
        mapping_keys=('group_id', 'assessment_group_id', 'pk', 'id'),
    )
    if not normalized_group_ids:
        return StudentAssessmentGroup.objects.none()
    return JournalAccessScope(
        academic_year,
        include_inactive=include_inactive,
    ).assessment_assignments(group_ids=normalized_group_ids)


def _normalized_primary_key_values(values, *, mapping_keys=('pk', 'id')) -> set[int]:
    """Normalize scalars, ``values()``, ``values_list()`` and model instances."""
    normalized: set[int] = set()
    if values is None:
        return normalized
    for raw_value in values:
        value = raw_value
        if isinstance(value, Mapping):
            value = next((value[key] for key in mapping_keys if key in value), None)
        elif isinstance(value, (tuple, list)) and len(value) == 1:
            value = value[0]
        elif hasattr(value, 'pk'):
            value = value.pk
        if value in (None, '') or isinstance(value, bool):
            continue
        try:
            primary_key = int(value)
        except (TypeError, ValueError, OverflowError):
            continue
        if primary_key > 0:
            normalized.add(primary_key)
    return normalized


def assessment_student_ids_by_group(
    group_ids,
    academic_year: AcademicYear,
    *,
    include_inactive: bool = False,
) -> dict[int, set[int]]:
    """Explicit group assignments are the only source of student access."""
    result: dict[int, set[int]] = {}
    for group_id, student_id in assessment_assignments_for_groups(
        group_ids,
        academic_year,
        include_inactive=include_inactive,
    ).values_list('assessment_group_id', 'student_id'):
        result.setdefault(group_id, set()).add(student_id)
    return result


def assessment_group_ids_for_student(
    student: Student,
    academic_year: AcademicYear,
    *,
    include_inactive: bool = False,
) -> set[int]:
    return set(
        JournalAccessScope(
            academic_year,
            include_inactive=include_inactive,
        ).assessment_assignments().filter(student=student).values_list(
            'assessment_group_id', flat=True
        )
    )


def enrollments_for_assessment_groups(
    group_ids,
    academic_year: AcademicYear,
    *,
    include_inactive: bool = False,
):
    normalized_group_ids = _normalized_primary_key_values(
        group_ids,
        mapping_keys=('group_id', 'assessment_group_id', 'pk', 'id'),
    )
    if not normalized_group_ids:
        return StudentEnrollment.objects.none()
    return JournalAccessScope(
        academic_year,
        include_inactive=include_inactive,
    ).assessment_enrollments(group_ids=normalized_group_ids)


def available_assessment_items_for_student(
    student: Student,
    academic_year: AcademicYear,
    *,
    subject: Subject | None = None,
    include_inactive: bool = False,
):
    queryset = JournalAccessScope(
        academic_year,
        include_inactive=include_inactive,
    ).assessment_items_for_student(student)
    if subject is not None:
        queryset = queryset.filter(group__subject=subject)
    return queryset


def assessment_items_visible_to_teacher(
    teacher: Teacher,
    academic_year: AcademicYear,
    *,
    include_inactive: bool = False,
):
    """A teacher sees works for which they are the responsible teacher."""
    return JournalAccessScope(
        academic_year,
        teacher=teacher,
        include_inactive=include_inactive,
    ).assessment_items()


def assessment_items_for_teacher(
    teacher: Teacher,
    academic_year: AcademicYear,
    *,
    include_inactive: bool = False,
):
    return assessment_items_visible_to_teacher(
        teacher, academic_year, include_inactive=include_inactive
    )


def enrollments_for_assessment_item(
    item: AssessmentItem,
    *,
    include_inactive: bool = False,
):
    return JournalAccessScope(
        item.group.academic_year,
        include_inactive=include_inactive,
    ).assessment_enrollments(group_ids=[item.group_id])


def students_for_assessment_item(item: AssessmentItem, *, include_inactive: bool = False):
    return Student.objects.filter(
        pk__in=enrollments_for_assessment_item(
            item, include_inactive=include_inactive
        ).values_list('student_id', flat=True)
    ).order_by('full_name', 'pk')


def enrollments_eligible_for_assessment_group(
    group: AssessmentGroup,
    *,
    include_inactive: bool = False,
):
    queryset = StudentEnrollment.objects.filter(academic_year=group.academic_year)
    if not include_inactive and group.academic_year.is_active:
        queryset = queryset.filter(is_active=True)
    return queryset.select_related('student', 'group', 'academic_year').order_by(
        'full_name', 'pk'
    )


def students_eligible_for_assessment_group(
    group: AssessmentGroup,
    *,
    include_inactive: bool = False,
):
    return Student.objects.filter(
        pk__in=enrollments_eligible_for_assessment_group(
            group, include_inactive=include_inactive
        ).values_list('student_id', flat=True)
    ).order_by('full_name', 'pk')


def teacher_can_edit_item(teacher: Teacher, item: AssessmentItem) -> bool:
    return JournalAccessScope(
        item.group.academic_year, teacher=teacher
    ).can_edit_assessment_item(item)


def _matching_rule(
    *,
    subject: Subject,
    academic_year: AcademicYear,
    group_ids: set[int],
    passed_count: int,
    all_required_satisfied: bool,
) -> FinalGradeRule | None:
    rules = list(
        FinalGradeRule.objects
        .filter(
            subject=subject,
            academic_year=academic_year,
            is_active=True,
        )
        .filter(
            Q(assessment_group__isnull=True)
            | Q(assessment_group_id__in=group_ids if len(group_ids) == 1 else [])
        )
        .select_related('assessment_group')
        .order_by('priority', 'pk')
    )
    for rule in rules:
        if rule.rule_type == FinalGradeRule.RULE_COUNT and rule.passed_count == passed_count:
            return rule
        if (
            rule.rule_type == FinalGradeRule.RULE_ALL_REQUIRED
            and rule.condition_value is all_required_satisfied
        ):
            return rule
        if rule.rule_type == FinalGradeRule.RULE_DEFAULT:
            return rule
    return None


def calculate_final_grade(
    student: Student,
    subject: Subject,
    academic_year: AcademicYear,
) -> FinalGradeCalculation:
    if not subject.uses_element_assessment:
        raise ValidationError('Автоматический расчёт доступен только для специального режима предмета.')

    items = list(
        available_assessment_items_for_student(
            student,
            academic_year,
            subject=subject,
        )
    )
    enrollment = student.enrollment_for_year(academic_year)
    result_by_item: dict[int, AssessmentResult] = {}
    if enrollment is not None and items:
        result_by_item = {
            result.item_id: result
            for result in AssessmentResult.objects.filter(
                enrollment=enrollment,
                item_id__in=[item.pk for item in items],
            )
        }

    passed_count = sum(
        1
        for item in items
        if result_by_item.get(item.pk)
        and result_by_item[item.pk].status == AssessmentResult.STATUS_PASSED
    )
    required_items = [item for item in items if item.is_required]
    passed_required_count = sum(
        1
        for item in required_items
        if result_by_item.get(item.pk)
        and result_by_item[item.pk].status == AssessmentResult.STATUS_PASSED
    )
    not_evaluated_count = sum(1 for item in items if item.pk not in result_by_item)
    all_required_satisfied = bool(required_items) and passed_required_count == len(required_items)
    group_ids = {item.group_id for item in items}

    rule = _matching_rule(
        subject=subject,
        academic_year=academic_year,
        group_ids=group_ids,
        passed_count=passed_count,
        all_required_satisfied=all_required_satisfied,
    )
    grade = rule.grade if rule is not None else 'Не рассчитано'
    return FinalGradeCalculation(
        grade=grade,
        passed_count=passed_count,
        total_count=len(items),
        required_count=len(required_items),
        passed_required_count=passed_required_count,
        not_evaluated_count=not_evaluated_count,
        all_required_satisfied=all_required_satisfied,
        rule_id=rule.pk if rule is not None else None,
        rule_type=rule.rule_type if rule is not None else None,
    )


@transaction.atomic
def recalculate_student_subject_final(
    student: Student,
    subject: Subject,
    academic_year: AcademicYear,
) -> SubjectResult:
    calculation = calculate_final_grade(student, subject, academic_year)
    enrollment = student.enrollment_for_year(academic_year)
    if enrollment is None:
        raise ValidationError('Ученик не зачислен в выбранный учебный год.')

    result, _created = SubjectResult.objects.get_or_create(
        student=student,
        subject=subject,
        academic_year=academic_year,
        defaults={'enrollment': enrollment},
    )
    result.enrollment = enrollment
    result.final_grade = calculation.grade
    result.is_auto_calculated = True
    result.calculation_details = calculation.as_details()
    result.calculated_at = timezone.now()
    result.save(allow_auto_update=True)
    return result


def recalculate_subject_finals(subject: Subject, academic_year: AcademicYear) -> int:
    student_ids = (
        StudentAssessmentGroup.objects
        .filter(
            assessment_group__subject=subject,
            assessment_group__academic_year=academic_year,
            is_active=True,
        )
        .values_list('student_id', flat=True)
        .distinct()
    )
    changed = 0
    for student in Student.objects.filter(pk__in=student_ids).iterator():
        recalculate_student_subject_final(student, subject, academic_year)
        changed += 1
    return changed


def recalculate_group_finals(group: AssessmentGroup) -> int:
    student_ids = (
        StudentAssessmentGroup.objects
        .filter(assessment_group=group, is_active=True)
        .values_list('student_id', flat=True)
    )
    changed = 0
    for student in Student.objects.filter(pk__in=student_ids).iterator():
        recalculate_student_subject_final(student, group.subject, group.academic_year)
        changed += 1
    return changed


@transaction.atomic
def set_assessment_result(
    *,
    item: AssessmentItem,
    student: Student,
    acting_teacher: Teacher,
    status: str,
    comment: str = '',
    return_created: bool = False,
) -> AssessmentResult | tuple[AssessmentResult, bool]:
    if not teacher_can_edit_item(acting_teacher, item):
        raise PermissionDenied('У преподавателя нет права изменять результаты этого произведения.')
    if status not in {AssessmentResult.STATUS_PASSED, AssessmentResult.STATUS_FAILED}:
        raise ValidationError({'status': 'Выберите «Зачёт» или «Незачёт».'})

    enrollment = student.enrollment_for_year(item.group.academic_year)
    if enrollment is None or not enrollments_for_assessment_item(item).filter(pk=enrollment.pk).exists():
        raise PermissionDenied('Это произведение не назначено выбранному ученику.')

    result, _created = AssessmentResult.objects.get_or_create(
        enrollment=enrollment,
        item=item,
        defaults={
            'status': status,
            'assessed_by': acting_teacher,
            'comment': comment,
        },
    )
    result.status = status
    result.assessed_by = acting_teacher
    result.comment = comment
    result.assessed_at = timezone.now()
    result.save(recalculate=False)
    recalculate_student_subject_final(student, item.group.subject, item.group.academic_year)
    if return_created:
        return result, _created
    return result


@transaction.atomic
def clear_assessment_result(
    *,
    item: AssessmentItem,
    student: Student,
    acting_teacher: Teacher,
) -> bool:
    if not teacher_can_edit_item(acting_teacher, item):
        raise PermissionDenied('У преподавателя нет права изменять результаты этого произведения.')
    enrollment = student.enrollment_for_year(item.group.academic_year)
    if enrollment is None:
        return False
    deleted, _ = AssessmentResult.objects.filter(enrollment=enrollment, item=item).delete()
    recalculate_student_subject_final(student, item.group.subject, item.group.academic_year)
    return bool(deleted)


def assessment_rows_for_student(student: Student, academic_year: AcademicYear):
    items = list(available_assessment_items_for_student(student, academic_year))
    enrollment = student.enrollment_for_year(academic_year)
    results = {}
    if enrollment and items:
        results = {
            result.item_id: result
            for result in AssessmentResult.objects.filter(
                enrollment=enrollment,
                item_id__in=[item.pk for item in items],
            ).select_related('assessed_by')
        }
    return [
        {
            'item': item,
            'result': results.get(item.pk),
            'status_display': (
                results[item.pk].get_status_display()
                if item.pk in results
                else 'Не оценено'
            ),
        }
        for item in items
    ]


def assessment_sections_for_teacher(
    teacher: Teacher | None,
    academic_year: AcademicYear,
    *,
    subject: Subject | None = None,
    study_group=None,
    assessment_group: AssessmentGroup | None = None,
    item: AssessmentItem | None = None,
    student: Student | None = None,
):
    """Build works sections from the same access scope used by forms and APIs."""
    scope = JournalAccessScope(academic_year, teacher=teacher)
    items_queryset = scope.assessment_items()
    if subject is not None:
        items_queryset = items_queryset.filter(group__subject=subject)
    if assessment_group is not None:
        items_queryset = items_queryset.filter(group=assessment_group)
    if item is not None:
        items_queryset = items_queryset.filter(pk=item.pk)
    items = list(items_queryset)
    if not items:
        return []

    group_ids = {assessment_item.group_id for assessment_item in items}
    assignments = scope.assessment_assignments(group_ids=group_ids)
    student_ids_by_group: dict[int, set[int]] = {}
    for group_id, student_id in assignments.values_list(
        'assessment_group_id', 'student_id'
    ):
        student_ids_by_group.setdefault(group_id, set()).add(student_id)

    enrollments = scope.assessment_enrollments(group_ids=group_ids)
    if study_group is not None:
        enrollments = enrollments.filter(group=study_group)
    if student is not None:
        enrollments = enrollments.filter(student=student)
    enrollment_list = list(enrollments)
    enrollment_by_student = {row.student_id: row for row in enrollment_list}
    enrollment_ids = [row.pk for row in enrollment_list]
    all_student_ids = set(enrollment_by_student)

    result_by_pair = {
        (result.item_id, result.enrollment_id): result
        for result in scope.assessment_results(items=items).filter(
            enrollment_id__in=enrollment_ids,
        )
    }
    final_by_pair = {
        (result.student_id, result.subject_id): result
        for result in SubjectResult.objects.filter(
            student_id__in=all_student_ids,
            subject_id__in={assessment_item.group.subject_id for assessment_item in items},
            academic_year=academic_year,
        )
    }

    sections = []
    for assessment_item in items:
        item_enrollments = [
            enrollment_by_student[student_id]
            for student_id in student_ids_by_group.get(assessment_item.group_id, set())
            if student_id in enrollment_by_student
        ]
        item_enrollments.sort(key=lambda row: (row.full_name, row.pk))
        rows = [
            {
                'enrollment': enrollment,
                'student': enrollment.student,
                'result': result_by_pair.get((assessment_item.pk, enrollment.pk)),
                'final_result': final_by_pair.get((
                    enrollment.student_id, assessment_item.group.subject_id
                )),
            }
            for enrollment in item_enrollments
        ]
        passed_count = sum(
            1 for row in rows
            if row['result'] and row['result'].status == AssessmentResult.STATUS_PASSED
        )
        failed_count = sum(
            1 for row in rows
            if row['result'] and row['result'].status == AssessmentResult.STATUS_FAILED
        )
        sections.append({
            'item': assessment_item,
            'rows': rows,
            'can_edit': scope.can_edit_assessment_item(assessment_item),
            'student_count': len(rows),
            'passed_count': passed_count,
            'failed_count': failed_count,
            'not_evaluated_count': len(rows) - passed_count - failed_count,
        })
    return sections


def assessment_summary_for_teacher(sections: list[dict]) -> dict[str, int]:
    """Build a dashboard summary from already loaded teacher sections."""
    student_ids = {
        row['student'].pk
        for section in sections
        for row in section['rows']
    }
    return {
        'item_count': len(sections),
        'student_count': len(student_ids),
        'result_count': sum(section['student_count'] for section in sections),
        'passed_count': sum(section['passed_count'] for section in sections),
        'failed_count': sum(section['failed_count'] for section in sections),
        'not_evaluated_count': sum(
            section['not_evaluated_count'] for section in sections
        ),
    }


def assessment_subject_sections_for_student(
    rows: list[dict],
    final_results: dict[int, SubjectResult],
) -> list[dict]:
    """Group student rows by subject without issuing extra database queries."""
    sections_by_subject: dict[int, dict] = {}
    for row in rows:
        item = row['item']
        section = sections_by_subject.setdefault(
            item.group.subject_id,
            {
                'subject': item.group.subject,
                'rows': [],
                'groups': [],
                'group_ids': set(),
                'passed_count': 0,
                'failed_count': 0,
                'not_evaluated_count': 0,
                'required_count': 0,
                'required_passed_count': 0,
                'final_result': final_results.get(item.group.subject_id),
            },
        )
        section['rows'].append(row)
        if item.group_id not in section['group_ids']:
            section['group_ids'].add(item.group_id)
            section['groups'].append(item.group)
        if item.is_required:
            section['required_count'] += 1

        result = row['result']
        if result is None:
            section['not_evaluated_count'] += 1
        elif result.status == AssessmentResult.STATUS_PASSED:
            section['passed_count'] += 1
            if item.is_required:
                section['required_passed_count'] += 1
        else:
            section['failed_count'] += 1

    sections = []
    for section in sections_by_subject.values():
        section.pop('group_ids', None)
        section['item_count'] = len(section['rows'])
        section['progress_percent'] = (
            round(section['passed_count'] * 100 / section['item_count'])
            if section['item_count']
            else 0
        )
        sections.append(section)
    return sections
