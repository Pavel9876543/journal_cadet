from __future__ import annotations

from dataclasses import dataclass

from django.db.models import Exists, F, OuterRef, Q

from .models import (
    AcademicYear,
    AssessmentGroup,
    AssessmentItem,
    AssessmentResult,
    Grade,
    GroupSubject,
    Student,
    StudentAssessmentGroup,
    StudentEnrollment,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
)


@dataclass(frozen=True)
class JournalAccessScope:
    """One authoritative read/write scope for a journal page.

    Access is derived only from assignment records:

    * ``GroupSubject`` and ``StudentSubject`` for ordinary grades;
    * ``AssessmentItem.responsible_teacher`` for a teacher's works;
    * ``StudentAssessmentGroup`` for students assigned to a works group.

    ``StudentEnrollment`` is the canonical participation record. Helper
    teacher/user memberships, existing grades/results and denormalized snapshot
    fields never grant access. They may describe history, but cannot be a second
    source of truth. This keeps the admin page, teacher cabinet and dependent
    forms in agreement.
    """

    academic_year: AcademicYear | None
    teacher: Teacher | None = None
    include_inactive: bool = False

    @property
    def active_only(self) -> bool:
        return bool(
            self.academic_year is not None
            and self.academic_year.is_active
            and not self.include_inactive
        )

    def _standard_assignment_exists(self):
        """Boolean expression matching an outcome to its exact assignment.

        Keeping the student/enrollment and subject in the same predicate avoids
        the cross-product bug where a teacher assigned subject A to one group
        and subject B to one student could see unrelated A/B outcomes.
        """
        group_assignment = GroupSubject.objects.filter(
            group__academic_year=self.academic_year,
            group_id=OuterRef('enrollment__group_id'),
            subject_id=OuterRef('subject_id'),
        )
        individual_assignment = StudentSubject.objects.filter(
            academic_year=self.academic_year,
            student_id=OuterRef('enrollment__student_id'),
            subject_id=OuterRef('subject_id'),
        )
        if self.teacher is not None:
            group_assignment = group_assignment.filter(teacher=self.teacher)
            individual_assignment = individual_assignment.filter(teacher=self.teacher)
        if self.active_only:
            group_assignment = group_assignment.filter(
                is_active=True,
                group__is_active=True,
                subject__is_active=True,
            )
            individual_assignment = individual_assignment.filter(
                is_active=True,
                subject__is_active=True,
                student__enrollments__academic_year=self.academic_year,
                student__enrollments__is_active=True,
            )
        return Exists(group_assignment) | Exists(individual_assignment)

    def group_subjects(self):
        queryset = GroupSubject.objects.filter(
            group__academic_year=self.academic_year,
            subject__assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
        )
        if self.teacher is not None:
            queryset = queryset.filter(teacher=self.teacher)
        if self.active_only:
            queryset = queryset.filter(
                is_active=True,
                group__is_active=True,
                subject__is_active=True,
            )
        return queryset.select_related(
            'group', 'group__academic_year', 'subject', 'teacher'
        ).distinct()

    def student_subjects(self):
        queryset = StudentSubject.objects.filter(
            academic_year=self.academic_year,
            subject__assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
        )
        if self.teacher is not None:
            queryset = queryset.filter(teacher=self.teacher)
        if self.active_only:
            queryset = queryset.filter(
                is_active=True,
                subject__is_active=True,
                student__enrollments__academic_year=self.academic_year,
                student__enrollments__is_active=True,
            )
        return queryset.select_related(
            'student', 'subject', 'teacher', 'academic_year'
        ).distinct()

    def standard_groups(self):
        group_ids = self.group_subjects().values_list('group_id', flat=True)
        individual_student_ids = self.student_subjects().values_list(
            'student_id', flat=True
        )
        individual_enrollments = StudentEnrollment.objects.filter(
            academic_year=self.academic_year,
            student_id__in=individual_student_ids,
        ).exclude(group_id=None)
        if self.active_only:
            individual_enrollments = individual_enrollments.filter(is_active=True)
        individual_group_ids = individual_enrollments.values_list('group_id', flat=True)
        queryset = StudyGroup.objects.filter(
            academic_year=self.academic_year,
        ).filter(Q(pk__in=group_ids) | Q(pk__in=individual_group_ids))
        if self.active_only:
            queryset = queryset.filter(is_active=True)
        return queryset.select_related('academic_year').distinct().order_by('name', 'pk')

    def standard_subjects(self, *, group: StudyGroup | None = None, student: Student | None = None):
        group_assignments = self.group_subjects()
        individual_assignments = self.student_subjects()

        if group is not None:
            group_assignments = group_assignments.filter(group=group)
            group_enrollments = StudentEnrollment.objects.filter(
                academic_year=self.academic_year,
                group=group,
            )
            if self.active_only:
                group_enrollments = group_enrollments.filter(is_active=True)
            group_student_ids = group_enrollments.values_list('student_id', flat=True)
            individual_assignments = individual_assignments.filter(
                student_id__in=group_student_ids,
            )

        if student is not None:
            enrollment = StudentEnrollment.objects.filter(
                student=student,
                academic_year=self.academic_year,
            ).first()
            if enrollment is None or (self.active_only and not enrollment.is_active):
                return Subject.objects.none()
            if group is None and enrollment.group_id:
                group_assignments = group_assignments.filter(group_id=enrollment.group_id)
            individual_assignments = individual_assignments.filter(student=student)

        queryset = Subject.objects.filter(
            Q(pk__in=group_assignments.values_list('subject_id', flat=True))
            | Q(pk__in=individual_assignments.values_list('subject_id', flat=True))
        )
        if self.active_only:
            queryset = queryset.filter(is_active=True)
        return queryset.distinct().order_by('name', 'pk')

    def standard_enrollments(
        self,
        *,
        group: StudyGroup | None = None,
        subject: Subject | None = None,
    ):
        group_assignments = self.group_subjects()
        individual_assignments = self.student_subjects()
        if group is not None:
            group_assignments = group_assignments.filter(group=group)
            group_enrollments = StudentEnrollment.objects.filter(
                academic_year=self.academic_year,
                group=group,
            )
            if self.active_only:
                group_enrollments = group_enrollments.filter(is_active=True)
            group_student_ids = group_enrollments.values_list('student_id', flat=True)
            individual_assignments = individual_assignments.filter(
                student_id__in=group_student_ids,
            )
        if subject is not None:
            group_assignments = group_assignments.filter(subject=subject)
            individual_assignments = individual_assignments.filter(subject=subject)

        queryset = StudentEnrollment.objects.filter(
            academic_year=self.academic_year,
        ).filter(
            Q(group_id__in=group_assignments.values_list('group_id', flat=True))
            | Q(student_id__in=individual_assignments.values_list('student_id', flat=True))
        )
        if group is not None:
            queryset = queryset.filter(group=group)
        if self.active_only:
            queryset = queryset.filter(is_active=True)
        return queryset.select_related(
            'student', 'student__instrument', 'student__user',
            'group', 'academic_year',
        ).distinct().order_by('full_name', 'pk')

    def standard_students(self, **kwargs):
        return Student.objects.filter(
            pk__in=self.standard_enrollments(**kwargs).values_list('student_id', flat=True)
        ).distinct().order_by('full_name', 'pk')

    def standard_grades(
        self,
        *,
        group: StudyGroup | None = None,
        subject: Subject | None = None,
    ):
        """Return grades whose exact student/subject pair is assigned.

        Grade authorship is historical metadata.  Current visibility follows
        GroupSubject/StudentSubject, while the pair-level ``Exists`` predicate
        prevents unrelated combinations from leaking into a teacher cabinet.
        """
        queryset = Grade.objects.filter(
            academic_year=self.academic_year,
            enrollment__academic_year=self.academic_year,
            subject__assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
        )
        if group is not None:
            queryset = queryset.filter(enrollment__group=group)
        if subject is not None:
            queryset = queryset.filter(subject=subject)
        if self.active_only:
            queryset = queryset.filter(
                enrollment__is_active=True,
                subject__is_active=True,
            )
        queryset = queryset.filter(self._standard_assignment_exists())
        return queryset.select_related(
            'student', 'enrollment', 'enrollment__group',
            'subject', 'teacher', 'academic_year',
        ).distinct()

    def standard_subject_results(
        self,
        *,
        group: StudyGroup | None = None,
        subject: Subject | None = None,
    ):
        queryset = SubjectResult.objects.filter(
            academic_year=self.academic_year,
            enrollment__academic_year=self.academic_year,
            subject__assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
        )
        if group is not None:
            queryset = queryset.filter(enrollment__group=group)
        if subject is not None:
            queryset = queryset.filter(subject=subject)
        if self.active_only:
            queryset = queryset.filter(
                enrollment__is_active=True,
                subject__is_active=True,
            )
        queryset = queryset.filter(self._standard_assignment_exists())
        return queryset.select_related(
            'student', 'enrollment', 'enrollment__group',
            'subject', 'academic_year',
        ).distinct()

    def standard_teachers(
        self,
        *,
        group: StudyGroup | None = None,
        student: Student | None = None,
        subject: Subject | None = None,
    ):
        group_assignments = GroupSubject.objects.filter(
            group__academic_year=self.academic_year,
            subject__assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
        )
        individual_assignments = StudentSubject.objects.filter(
            academic_year=self.academic_year,
            subject__assessment_mode=Subject.ASSESSMENT_MODE_STANDARD,
        )
        if group is not None:
            group_assignments = group_assignments.filter(group=group)
            group_enrollments = StudentEnrollment.objects.filter(
                academic_year=self.academic_year,
                group=group,
            )
            if self.active_only:
                group_enrollments = group_enrollments.filter(is_active=True)
            student_ids = group_enrollments.values_list('student_id', flat=True)
            individual_assignments = individual_assignments.filter(student_id__in=student_ids)
        if student is not None:
            enrollment = StudentEnrollment.objects.filter(
                academic_year=self.academic_year,
                student=student,
            ).first()
            if enrollment is None or (self.active_only and not enrollment.is_active):
                return Teacher.objects.none()
            if group is None and enrollment.group_id:
                group_assignments = group_assignments.filter(group_id=enrollment.group_id)
            individual_assignments = individual_assignments.filter(student=student)
        if subject is not None:
            group_assignments = group_assignments.filter(subject=subject)
            individual_assignments = individual_assignments.filter(subject=subject)
        if self.active_only:
            group_assignments = group_assignments.filter(
                is_active=True, group__is_active=True, subject__is_active=True
            )
            individual_assignments = individual_assignments.filter(
                is_active=True,
                subject__is_active=True,
                student__enrollments__academic_year=self.academic_year,
                student__enrollments__is_active=True,
            )
        queryset = Teacher.objects.filter(
            Q(pk__in=group_assignments.values_list('teacher_id', flat=True))
            | Q(pk__in=individual_assignments.values_list('teacher_id', flat=True))
        )
        return queryset.distinct().order_by('full_name', 'pk')

    def assessment_items(self):
        queryset = AssessmentItem.objects.filter(
            group__academic_year=self.academic_year,
            group__subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            responsible_teacher__isnull=False,
        )
        if self.teacher is not None:
            queryset = queryset.filter(responsible_teacher=self.teacher)
        if self.active_only:
            queryset = queryset.filter(
                is_active=True,
                group__is_active=True,
                group__subject__is_active=True,
            )
        return queryset.select_related(
            'group', 'group__academic_year', 'group__subject',
            'subject', 'academic_year', 'responsible_teacher',
        ).distinct().order_by(
            'group__subject__name', 'group__sort_order', 'group__name',
            'sort_order', 'title', 'pk',
        )

    def assessment_groups(self):
        group_ids = self.assessment_items().values_list('group_id', flat=True)
        queryset = AssessmentGroup.objects.filter(
            pk__in=group_ids,
            academic_year=self.academic_year,
        )
        if self.active_only:
            queryset = queryset.filter(is_active=True, subject__is_active=True)
        return queryset.select_related('subject', 'academic_year').distinct().order_by(
            'subject__name', 'sort_order', 'name', 'pk'
        )

    def assessment_assignments(self, *, group_ids=None):
        if group_ids is None:
            group_ids = self.assessment_groups().values_list('pk', flat=True)
        queryset = StudentAssessmentGroup.objects.filter(
            assessment_group_id__in=group_ids,
            assessment_group__academic_year=self.academic_year,
        )
        if self.active_only:
            queryset = queryset.filter(is_active=True)
        return queryset.select_related(
            'student', 'assessment_group', 'assessment_group__subject',
            'assessment_group__academic_year',
        ).distinct()

    def assessment_enrollments(self, *, group_ids=None):
        student_ids = self.assessment_assignments(group_ids=group_ids).values_list(
            'student_id', flat=True
        )
        queryset = StudentEnrollment.objects.filter(
            academic_year=self.academic_year,
            student_id__in=student_ids,
        )
        if self.active_only:
            queryset = queryset.filter(is_active=True)
        return queryset.select_related(
            'student', 'student__instrument', 'student__user',
            'group', 'academic_year',
        ).distinct().order_by('full_name', 'pk')

    def assessment_students(self, *, group_ids=None):
        return Student.objects.filter(
            pk__in=self.assessment_enrollments(group_ids=group_ids).values_list(
                'student_id', flat=True
            )
        ).distinct().order_by('full_name', 'pk')

    def assessment_items_for_student(self, student: Student):
        group_ids = self.assessment_assignments().filter(
            student=student,
        ).values_list('assessment_group_id', flat=True)
        return self.assessment_items().filter(group_id__in=group_ids)

    def assessment_results(self, *, items=None, group_ids=None):
        scoped_items = self.assessment_items()
        if group_ids is not None:
            scoped_items = scoped_items.filter(group_id__in=group_ids)
        if items is not None:
            scoped_items = scoped_items.filter(
                pk__in=items.values_list('pk', flat=True)
                if hasattr(items, 'values_list')
                else [item.pk for item in items]
            )
        enrollments = self.assessment_enrollments(
            group_ids=scoped_items.values_list('group_id', flat=True),
        )
        return AssessmentResult.objects.filter(
            item_id__in=scoped_items.values_list('pk', flat=True),
            enrollment_id__in=enrollments.values_list('pk', flat=True),
        ).select_related(
            'item', 'item__group', 'item__group__subject',
            'enrollment', 'enrollment__student', 'assessed_by',
        )

    def can_edit_assessment_item(self, item: AssessmentItem) -> bool:
        if self.academic_year is None or not self.active_only:
            return False
        if (
            item.group.academic_year_id != self.academic_year.pk
            or not item.is_active
            or not item.group.is_active
            or not item.group.subject.is_active
        ):
            return False
        if self.teacher is None:
            return True
        return item.responsible_teacher_id == self.teacher.pk

    def can_edit_standard(self, enrollment: StudentEnrollment, subject: Subject) -> bool:
        if not self.active_only:
            return False
        if (
            enrollment.academic_year_id != self.academic_year.pk
            or not enrollment.is_active
            or not subject.is_active
        ):
            return False
        if self.teacher is None:
            return True
        return bool(
            GroupSubject.objects.filter(
                group_id=enrollment.group_id,
                group__academic_year=self.academic_year,
                group__is_active=True,
                subject=subject,
                subject__is_active=True,
                teacher=self.teacher,
                is_active=True,
            ).exists()
            or StudentSubject.objects.filter(
                student_id=enrollment.student_id,
                academic_year=self.academic_year,
                subject=subject,
                subject__is_active=True,
                teacher=self.teacher,
                is_active=True,
            ).exists()
        )


def teacher_assignment_years(teacher: Teacher):
    """Years with real teacher assignments, independent of helper rows.

    An archived assignment remains visible for history.  In the active year an
    inactive assignment must not create an empty cabinet year.
    """
    group_year_ids = GroupSubject.objects.filter(teacher=teacher).filter(
        Q(group__academic_year__is_active=False)
        | Q(is_active=True, group__is_active=True, subject__is_active=True)
    ).values_list('group__academic_year_id', flat=True)
    individual_year_ids = StudentSubject.objects.filter(teacher=teacher).filter(
        Q(academic_year__is_active=False)
        | Q(
            is_active=True,
            subject__is_active=True,
            student__enrollments__academic_year=F('academic_year'),
            student__enrollments__is_active=True,
        )
    ).values_list('academic_year_id', flat=True)
    assessment_year_ids = AssessmentItem.objects.filter(
        responsible_teacher=teacher,
    ).filter(
        Q(group__academic_year__is_active=False)
        | Q(is_active=True, group__is_active=True, group__subject__is_active=True)
    ).values_list('group__academic_year_id', flat=True)
    return AcademicYear.objects.filter(
        Q(pk__in=group_year_ids)
        | Q(pk__in=individual_year_ids)
        | Q(pk__in=assessment_year_ids)
    ).distinct().order_by('-starts_on', '-pk')
