from __future__ import annotations

from collections import Counter

from django.core.management.base import BaseCommand
from django.db import transaction

from journal.access_scope import JournalAccessScope
from journal.models import (
    AcademicYear,
    AssessmentItem,
    AssessmentResult,
    GroupSubject,
    StudentAssessmentGroup,
    StudentEnrollment,
    StudentSubject,
    Subject,
    Teacher,
    ensure_teacher_academic_year_membership,
    ensure_teacher_subject,
)


class Command(BaseCommand):
    help = (
        'Проверяет канонические связи журнала. С флагом --fix исправляет только '
        'дублирующие поля и служебные записи, не создавая назначений из оценок.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--fix',
            action='store_true',
            help='Безопасно синхронизировать найденные дублирующие поля.',
        )
        parser.add_argument(
            '--teacher-id',
            type=int,
            help='Показать фактический объём доступа конкретного преподавателя.',
        )
        parser.add_argument(
            '--academic-year-id',
            type=int,
            help='Учебный год для диагностики преподавателя; по умолчанию активный.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        fix = options['fix']
        counters = Counter()

        for item in AssessmentItem.objects.select_related(
            'group', 'group__subject', 'group__academic_year'
        ).iterator():
            updates = {}
            if item.subject_id != item.group.subject_id:
                counters['assessment_item_subject_mismatch'] += 1
                updates['subject_id'] = item.group.subject_id
            if item.academic_year_id != item.group.academic_year_id:
                counters['assessment_item_year_mismatch'] += 1
                updates['academic_year_id'] = item.group.academic_year_id
            if fix and updates:
                AssessmentItem.objects.filter(pk=item.pk).update(**updates)
            if fix and item.responsible_teacher_id:
                ensure_teacher_academic_year_membership(
                    item.responsible_teacher_id,
                    item.group.academic_year_id,
                )
                ensure_teacher_subject(
                    item.responsible_teacher_id,
                    item.group.subject_id,
                )

        if fix:
            for assignment in GroupSubject.objects.select_related('group').iterator():
                ensure_teacher_academic_year_membership(
                    assignment.teacher_id,
                    assignment.group.academic_year_id,
                )
                ensure_teacher_subject(
                    assignment.teacher_id,
                    assignment.subject_id,
                )
            for assignment in StudentSubject.objects.iterator():
                ensure_teacher_academic_year_membership(
                    assignment.teacher_id,
                    assignment.academic_year_id,
                )
                ensure_teacher_subject(
                    assignment.teacher_id,
                    assignment.subject_id,
                )

        seen_pairs: set[tuple[int, int]] = set()
        for assignment in StudentAssessmentGroup.objects.select_related(
            'assessment_group', 'assessment_group__academic_year'
        ).order_by('pk').iterator():
            pair = (assignment.student_id, assignment.assessment_group_id)
            if pair in seen_pairs:
                counters['duplicate_student_assessment_group'] += 1
            seen_pairs.add(pair)

            canonical_year_id = assignment.assessment_group.academic_year_id
            updates = {}
            if assignment.academic_year_id != canonical_year_id:
                counters['assessment_assignment_year_mismatch'] += 1
                updates['academic_year_id'] = canonical_year_id
            enrollment_id = StudentEnrollment.objects.filter(
                student_id=assignment.student_id,
                academic_year_id=canonical_year_id,
            ).values_list('pk', flat=True).first()
            if enrollment_id is None:
                counters['assessment_assignment_missing_enrollment'] += 1
            elif assignment.enrollment_id != enrollment_id:
                counters['assessment_assignment_enrollment_mismatch'] += 1
                updates['enrollment_id'] = enrollment_id
            if fix and updates:
                StudentAssessmentGroup.objects.filter(pk=assignment.pk).update(**updates)


        explicit_assignment_pairs = set(
            StudentAssessmentGroup.objects.values_list(
                'student_id', 'assessment_group_id'
            )
        )
        for result in AssessmentResult.objects.select_related(
            'enrollment', 'item', 'item__group'
        ).iterator():
            pair = (result.enrollment.student_id, result.item.group_id)
            if pair not in explicit_assignment_pairs:
                counters['assessment_result_without_assignment'] += 1
            if result.enrollment.academic_year_id != result.item.group.academic_year_id:
                counters['assessment_result_year_mismatch'] += 1

        informational = {
            'element_group_subject_assignments': GroupSubject.objects.filter(
                subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            ).count(),
            'element_individual_subject_assignments': StudentSubject.objects.filter(
                subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            ).count(),
        }

        labels = {
            'assessment_item_subject_mismatch': 'Произведения с неверным предметом',
            'assessment_item_year_mismatch': 'Произведения с неверным учебным годом',
            'duplicate_student_assessment_group': 'Повторные назначения ученик/группа',
            'assessment_assignment_year_mismatch': 'Назначения с неверным учебным годом',
            'assessment_assignment_missing_enrollment': 'Назначения без зачисления ученика',
            'assessment_assignment_enrollment_mismatch': 'Назначения с неверным зачислением',
            'assessment_result_without_assignment': 'Результаты без явного назначения ученика',
            'assessment_result_year_mismatch': 'Результаты с несовместимым учебным годом',
            'element_group_subject_assignments': 'Спецпредметы в обычных назначениях групп',
            'element_individual_subject_assignments': 'Спецпредметы в обычных индивидуальных назначениях',
        }
        integrity_keys = [
            key for key in labels if not key.startswith('element_')
        ]
        for key in integrity_keys:
            self.stdout.write(f'{labels[key]}: {counters[key]}')
        self.stdout.write('Информационные сведения:')
        for key, value in informational.items():
            self.stdout.write(f'  {labels[key]}: {value}')

        total = sum(counters[key] for key in integrity_keys)

        teacher_id = options.get('teacher_id')
        if teacher_id:
            teacher = Teacher.objects.filter(pk=teacher_id).first()
            academic_year = (
                AcademicYear.objects.filter(pk=options.get('academic_year_id')).first()
                if options.get('academic_year_id')
                else AcademicYear.objects.filter(is_active=True).first()
            )
            if teacher is None:
                self.stdout.write(self.style.ERROR(
                    f'Преподаватель с id={teacher_id} не найден.'
                ))
            elif academic_year is None:
                self.stdout.write(self.style.ERROR('Учебный год не найден.'))
            else:
                scope = JournalAccessScope(academic_year, teacher=teacher)
                self.stdout.write('')
                self.stdout.write(
                    f'Доступ преподавателя {teacher.pk} за {academic_year.name}:'
                )
                self.stdout.write(
                    f'  групповых назначений: {scope.group_subjects().count()}'
                )
                self.stdout.write(
                    f'  индивидуальных назначений: {scope.student_subjects().count()}'
                )
                self.stdout.write(
                    f'  обычных учеников: {scope.standard_students().count()}'
                )
                self.stdout.write(
                    f'  видимых обычных оценок: {scope.standard_grades().count()}'
                )
                self.stdout.write(
                    f'  ответственных произведений: {scope.assessment_items().count()}'
                )
                self.stdout.write(
                    f'  учеников в группах произведений: {scope.assessment_students().count()}'
                )
                self.stdout.write(
                    f'  видимых результатов сдачи: {scope.assessment_results().count()}'
                )

        if fix:
            self.stdout.write(self.style.SUCCESS(
                f'Проверка завершена, безопасные исправления применены. Найдено: {total}.'
            ))
        elif total:
            self.stdout.write(self.style.WARNING(
                'Обнаружены несогласованные связи. --fix исправляет только безопасные '
                'дублирующие поля; назначения и результаты проверьте вручную.'
            ))
        else:
            self.stdout.write(self.style.SUCCESS('Связи журнала согласованы.'))
