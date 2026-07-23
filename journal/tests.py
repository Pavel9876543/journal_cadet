from __future__ import annotations

from datetime import date
from threading import Barrier, Lock, Thread
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import skipUnless
from unittest.mock import patch

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import CommandError, call_command
from django.db import IntegrityError, close_old_connections, connection, transaction
from django.db.models import Count, Q
from django.test import Client, RequestFactory, TestCase, TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from openpyxl import load_workbook

from journal.academic_year_context import filter_temporary_credentials_for_year
from journal.assignment_options import assignment_teacher_queryset
from journal.account_utils import (
    build_course_application_login,
    build_display_name_from_full_name,
    build_username_from_full_name,
    display_name_for_user,
    ensure_temporary_credential_for_user,
    generate_temporary_password,
)
from journal.admin import (
    AcademicYearAdmin,
    CourseRegistrationSettingsAdmin,
    GradeAdmin,
    GradeAdminForm,
    GroupSubjectAdminForm,
    StudentAdmin,
    StudentAdminForm,
    StudentInline,
    StudentSubjectAdminForm,
    StudyGroupAdmin,
    PasswordRecoveryContactAdmin,
    TemporaryCredentialAdmin,
    TeacherAdmin,
    SubjectResultAdminForm,
    TeacherAdminForm,
)
from journal.forms import (
    CourseApplicationAdminForm,
    CourseApplicationPublicForm,
    CourseRegistrationSettingsForm,
    DetailedPasswordChangeForm,
    GradeCreateForm,
    SubjectResultForm,
    get_student_allowed_subjects,
    get_student_subject_teachers,
    get_teacher_groups,
    get_teacher_subjects,
)
from journal.grade_options import (
    get_grade_groups,
    get_grade_students,
    get_grade_subjects,
    get_grade_teachers,
)
from journal.registration_utils import minimum_birth_date_for_age, normalize_parent_contacts
from journal.views import (
    _build_journal_tables,
    _is_duplicate_course_application_phone_error,
)
from journal.models import (
    AcademicYear,
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    GroupSubject,
    Instrument,
    PasswordRecoveryContact,
    Student,
    StudentEnrollment,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TeacherEnrollment,
    TeacherSubject,
    TemporaryCredential,
)


User = get_user_model()


class JournalTestDataMixin:
    """Фабрики для тестов новой архитектуры журнала."""

    def create_academic_year(self, *, name='2025/2026', is_active=True):
        start_year = int(name.split('/', 1)[0])
        return AcademicYear.objects.create(
            name=name,
            starts_on=date(start_year, 9, 1),
            ends_on=date(start_year + 1, 8, 31),
            is_active=is_active,
        )

    def create_group(self, *, name='Группа А', academic_year=None):
        academic_year = academic_year or self.create_academic_year()
        return StudyGroup.objects.create(name=name, academic_year=academic_year)

    def create_instrument(self, *, name='Баян'):
        return Instrument.objects.create(name=name)

    def create_subject(
        self,
        *,
        name='Сольфеджио',
        is_specialty=False,
        final_grade_type=None,
    ):
        return Subject.objects.create(
            name=name,
            is_specialty=is_specialty,
            final_grade_type=final_grade_type or Subject.FINAL_GRADE_TYPE_NUMERIC,
        )

    def create_teacher(
        self,
        *,
        full_name='Иванов Иван Иванович',
        username='teacher',
    ):
        user = User.objects.create_user(username=username, password='Pass12345!')
        return Teacher.objects.create(full_name=full_name, user=user)

    def create_student(
        self,
        *,
        full_name='Петров Пётр Петрович',
        group=None,
        instrument=None,
        username='student',
    ):
        group = group or self.create_group()
        instrument = instrument or self.create_instrument()
        user = User.objects.create_user(username=username, password='Pass12345!')
        return Student.objects.create(
            full_name=full_name,
            group=group,
            instrument=instrument,
            user=user,
        )

    def create_group_assignment(self, *, group=None, subject=None, teacher=None):
        group = group or self.create_group()
        subject = subject or self.create_subject()
        teacher = teacher or self.create_teacher()
        return GroupSubject.objects.create(
            group=group,
            subject=subject,
            teacher=teacher,
        )

    def create_individual_assignment(
        self,
        *,
        student=None,
        subject=None,
        teacher=None,
        is_specialty=True,
    ):
        student = student or self.create_student()
        subject = subject or self.create_subject(
            name='Специальность',
            is_specialty=True,
        )
        teacher = teacher or self.create_teacher(username='specialty_teacher')
        return StudentSubject.objects.create(
            student=student,
            subject=subject,
            teacher=teacher,
            is_specialty=is_specialty,
        )

    def create_base_journal(self):
        year = self.create_academic_year()
        group = self.create_group(academic_year=year)
        instrument = self.create_instrument(name='Баян')
        solfeggio = self.create_subject(name='Сольфеджио')
        literature = self.create_subject(name='Музыкальная литература')
        specialty = self.create_subject(name='Специальность', is_specialty=True)

        teacher = self.create_teacher(
            full_name='Иванов Иван Иванович',
            username='teacher_ivanov',
        )
        other_teacher = self.create_teacher(
            full_name='Петров Пётр Петрович',
            username='teacher_petrov',
        )
        student = self.create_student(
            full_name='Сидоров Семён Семёнович',
            group=group,
            instrument=instrument,
            username='student_sidorov',
        )

        GroupSubject.objects.create(
            group=group,
            subject=solfeggio,
            teacher=teacher,
        )
        GroupSubject.objects.create(
            group=group,
            subject=literature,
            teacher=other_teacher,
        )
        StudentSubject.objects.create(
            student=student,
            subject=specialty,
            teacher=other_teacher,
            is_specialty=True,
        )

        return {
            'year': year,
            'group': group,
            'instrument': instrument,
            'solfeggio': solfeggio,
            'literature': literature,
            'specialty': specialty,
            'teacher': teacher,
            'other_teacher': other_teacher,
            'student': student,
        }

    def application_payload(self, **overrides):
        payload = {
            'last_name': 'Иванов',
            'first_name': 'Иван',
            'middle_name': 'Иванович',
            'gender': CourseApplication.GENDER_MALE,
            'birth_date': date(2000, 1, 1),
            'city_church': 'Тамбов',
            'instrument': 'Баян I',
            'music_education': CourseApplication.MUSIC_EDUCATION_NONE,
            'student_phone': '+7 (999) 123-45-67',
            'parent_contacts': '',
            'comments': '',
        }
        payload.update(overrides)
        return payload

    def application_form_payload(self, **overrides):
        payload = self.application_payload(**overrides)
        birth_date = payload['birth_date']

        if hasattr(birth_date, 'isoformat'):
            payload['birth_date'] = birth_date.isoformat()

        return payload


class AcademicStructureModelTests(JournalTestDataMixin, TestCase):
    def test_only_one_academic_year_can_be_active_after_save(self):
        first = self.create_academic_year(name='2025/2026', is_active=True)
        second = AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
            is_active=True,
        )

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

    def test_newest_academic_year_becomes_active_even_when_created_inactive(self):
        first = self.create_academic_year(name='2025/2026', is_active=True)
        second = AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
            is_active=False,
        )

        first.refresh_from_db()
        second.refresh_from_db()

        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)

    def test_new_year_preserves_old_enrollment_and_grade_snapshots(self):
        data = self.create_base_journal()
        old_name = data['student'].full_name
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            academic_year=data['year'],
            date=date(2025, 10, 10),
            value='5',
        )

        new_year = self.create_academic_year(name='2026/2027')
        data['student'].refresh_from_db()
        data['year'].refresh_from_db()
        grade.refresh_from_db()

        self.assertFalse(data['year'].is_active)
        self.assertTrue(new_year.is_active)
        self.assertIsNone(data['student'].group_id)
        self.assertFalse(data['student'].is_active)
        self.assertEqual(grade.enrollment.group_id, data['group'].pk)
        self.assertEqual(grade.student_name_snapshot, old_name)

        data['student'].full_name = 'Новое имя ученика'
        data['student'].save()
        old_enrollment = StudentEnrollment.objects.get(
            student=data['student'],
            academic_year=data['year'],
        )
        self.assertEqual(old_enrollment.full_name, old_name)

    def test_new_year_resets_group_for_every_student_from_previous_year(self):
        year = self.create_academic_year()
        first_group = self.create_group(name='Первая группа', academic_year=year)
        second_group = self.create_group(name='Вторая группа', academic_year=year)
        instrument = self.create_instrument()
        first = self.create_student(
            full_name='Первый Ученик',
            group=first_group,
            instrument=instrument,
            username='first_student',
        )
        second = self.create_student(
            full_name='Второй Ученик',
            group=second_group,
            instrument=instrument,
            username='second_student',
        )

        self.create_academic_year(name='2026/2027')
        first.refresh_from_db()
        second.refresh_from_db()

        self.assertIsNone(first.group_id)
        self.assertIsNone(second.group_id)
        self.assertFalse(first.is_active)
        self.assertFalse(second.is_active)

    def test_teacher_membership_is_scoped_to_academic_year(self):
        old_year = self.create_academic_year()
        teacher = self.create_teacher()

        self.assertTrue(
            TeacherEnrollment.objects.filter(
                teacher=teacher,
                academic_year=old_year,
                is_active=True,
            ).exists(),
        )

        new_year = self.create_academic_year(name='2026/2027')
        teacher.refresh_from_db()
        self.assertFalse(teacher.is_active)
        self.assertFalse(
            TeacherEnrollment.objects.filter(
                teacher=teacher,
                academic_year=new_year,
            ).exists(),
        )

        new_group = self.create_group(name='Новая группа', academic_year=new_year)
        subject = self.create_subject()
        GroupSubject.objects.create(group=new_group, subject=subject, teacher=teacher)

        teacher.refresh_from_db()
        self.assertTrue(teacher.is_active)
        self.assertTrue(
            TeacherEnrollment.objects.filter(
                teacher=teacher,
                academic_year=new_year,
                is_active=True,
            ).exists(),
        )

    def test_new_year_finalizes_current_names_before_archiving(self):
        data = self.create_base_journal()
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            academic_year=data['year'],
            date=date(2025, 10, 10),
            value='5',
        )
        data['student'].full_name = 'Итоговое имя ученика'
        data['student'].save()
        data['solfeggio'].name = 'Итоговое название предмета'
        data['solfeggio'].save()
        data['teacher'].full_name = 'Итоговое имя преподавателя'
        data['teacher'].save()

        self.create_academic_year(name='2026/2027')
        grade.refresh_from_db()
        assignment = GroupSubject.objects.get(
            group=data['group'],
            subject=data['solfeggio'],
        )
        enrollment = StudentEnrollment.objects.get(
            student=data['student'],
            academic_year=data['year'],
        )

        self.assertEqual(enrollment.full_name, 'Итоговое имя ученика')
        self.assertEqual(assignment.subject_name_snapshot, 'Итоговое название предмета')
        self.assertEqual(assignment.teacher_name_snapshot, 'Итоговое имя преподавателя')
        self.assertEqual(grade.student_name_snapshot, 'Итоговое имя ученика')
        self.assertEqual(grade.subject_name_snapshot, 'Итоговое название предмета')
        self.assertEqual(grade.teacher_name_snapshot, 'Итоговое имя преподавателя')

    def test_reordering_active_year_finalizes_it_and_restores_latest_enrollment(self):
        data = self.create_base_journal()
        current_year = self.create_academic_year(name='2027/2028')
        current_group = self.create_group(name='Группа Б', academic_year=current_year)
        GroupSubject.objects.create(
            group=current_group,
            subject=data['solfeggio'],
            teacher=data['teacher'],
        )

        data['student'].group = current_group
        data['student'].full_name = 'Финальное имя второго года'
        data['student'].save()
        current_grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            academic_year=current_year,
            date=date(2027, 10, 10),
            value='5',
        )

        current_year.name = '2024/2025'
        current_year.starts_on = date(2024, 9, 1)
        current_year.ends_on = date(2025, 8, 31)
        current_year.save()

        data['year'].refresh_from_db()
        current_year.refresh_from_db()
        data['student'].refresh_from_db()
        current_grade.refresh_from_db()
        current_enrollment = StudentEnrollment.objects.get(
            student=data['student'],
            academic_year=current_year,
        )

        self.assertTrue(data['year'].is_active)
        self.assertFalse(current_year.is_active)
        self.assertEqual(data['student'].group_id, data['group'].pk)
        self.assertEqual(current_enrollment.full_name, 'Финальное имя второго года')
        self.assertEqual(current_grade.student_name_snapshot, 'Финальное имя второго года')

    def test_deleting_empty_active_year_restores_previous_student_groups(self):
        data = self.create_base_journal()
        empty_year = self.create_academic_year(name='2026/2027')
        data['student'].refresh_from_db()
        self.assertIsNone(data['student'].group_id)

        empty_year.delete()

        data['year'].refresh_from_db()
        data['student'].refresh_from_db()
        self.assertTrue(data['year'].is_active)
        self.assertEqual(data['student'].group_id, data['group'].pk)
        self.assertTrue(data['student'].is_active)

    def test_archived_grade_cannot_be_changed_or_deleted(self):
        data = self.create_base_journal()
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            academic_year=data['year'],
            date=date(2025, 10, 10),
            value='5',
        )
        self.create_academic_year(name='2026/2027')

        grade.value = '4'
        with self.assertRaisesMessage(ValidationError, 'Архивный учебный год'):
            grade.save()
        with self.assertRaisesMessage(ValidationError, 'Архивный учебный год'):
            grade.delete()

    def test_academic_year_periods_cannot_overlap(self):
        self.create_academic_year()

        with self.assertRaisesMessage(ValidationError, 'пересекается'):
            AcademicYear.objects.create(
                name='Пересекающийся',
                starts_on=date(2026, 8, 1),
                ends_on=date(2027, 7, 31),
            )

    def test_cannot_save_group_in_archived_academic_year(self):
        group = self.create_group()
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )

        group.name = 'Переименованная группа'

        with self.assertRaisesMessage(ValidationError, 'Архивный учебный год'):
            group.save()

    def test_grade_date_must_be_inside_selected_academic_year(self):
        data = self.create_base_journal()

        with self.assertRaisesMessage(ValidationError, 'Дата оценки должна попадать в период'):
            Grade.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
                academic_year=data['year'],
                date=date(2024, 10, 1),
                value='5',
            )

    def test_grade_cannot_be_created_in_archived_academic_year(self):
        data = self.create_base_journal()
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )

        with self.assertRaisesMessage(ValidationError, 'Архивный учебный год'):
            Grade.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
                academic_year=data['year'],
                date=date(2025, 10, 1),
                value='5',
            )

    def test_subject_final_grade_type_cannot_break_existing_results(self):
        data = self.create_base_journal()
        pass_fail_subject = self.create_subject(
            name='Зачетный предмет',
            final_grade_type=Subject.FINAL_GRADE_TYPE_PASS_FAIL,
        )
        GroupSubject.objects.create(
            group=data['group'],
            subject=pass_fail_subject,
            teacher=data['teacher'],
        )
        SubjectResult.objects.create(
            student=data['student'],
            subject=pass_fail_subject,
            academic_year=data['year'],
            final_grade='Зачет',
        )

        pass_fail_subject.final_grade_type = Subject.FINAL_GRADE_TYPE_NUMERIC

        with self.assertRaisesMessage(ValidationError, 'Нельзя изменить тип итоговой оценки'):
            pass_fail_subject.save()

    def test_group_subject_links_group_subject_and_teacher(self):
        data = self.create_base_journal()

        assignment = GroupSubject.objects.get(
            group=data['group'],
            subject=data['solfeggio'],
        )

        self.assertEqual(assignment.teacher, data['teacher'])
        self.assertIn(data['solfeggio'], data['group'].subjects.all())
        self.assertEqual(data['teacher'].group_subjects.count(), 1)

    def test_student_group_is_optional_and_cleared_when_group_is_deleted(self):
        group = self.create_group()
        student = self.create_student(group=group)

        group.delete()
        student.refresh_from_db()

        self.assertIsNone(student.group)

    def test_deleting_active_enrollment_clears_current_student_group(self):
        group = self.create_group()
        student = self.create_student(group=group)
        enrollment = student.enrollment_for_year(group.academic_year)

        enrollment.delete()
        student.refresh_from_db()

        self.assertIsNone(student.group)
        self.assertFalse(
            StudentEnrollment.objects.filter(
                student=student,
                academic_year=group.academic_year,
            ).exists(),
        )

    def test_only_academic_year_cannot_be_deleted(self):
        academic_year = self.create_academic_year()

        with self.assertRaisesMessage(ValidationError, 'единственный учебный год'):
            academic_year.delete()

        self.assertTrue(AcademicYear.objects.filter(pk=academic_year.pk, is_active=True).exists())

    def test_archived_academic_year_cannot_be_deleted(self):
        archived_year = self.create_academic_year()
        self.create_academic_year(name='2026/2027')

        with self.assertRaisesMessage(ValidationError, 'Архивный учебный год'):
            archived_year.delete()

        self.assertTrue(AcademicYear.objects.filter(pk=archived_year.pk).exists())

    def test_student_without_group_keeps_individual_subjects_display(self):
        data = self.create_base_journal()
        data['student'].group = None
        data['student'].save(update_fields=['group'])

        self.assertIn('Специальность', data['student'].subjects_display)

    def test_group_subject_rejects_specialty_subject(self):
        group = self.create_group()
        specialty = self.create_subject(name='Специальность', is_specialty=True)
        teacher = self.create_teacher()

        with self.assertRaises(ValidationError):
            GroupSubject.objects.create(
                group=group,
                subject=specialty,
                teacher=teacher,
            )

    def test_subject_specialty_flag_is_labeled_as_individual_subject(self):
        self.assertEqual(
            Subject._meta.get_field('is_specialty').verbose_name,
            'Индивидуальный предмет',
        )

    def test_student_subject_accepts_specialty_subject(self):
        data = self.create_base_journal()
        student = data['student']

        self.assertEqual(student.specialty_subject, data['specialty'])
        self.assertEqual(student.specialty_teacher, data['other_teacher'])
        self.assertIn('Специальность', student.subjects_display)

    def test_student_subject_rejects_group_subject(self):
        data = self.create_base_journal()

        with self.assertRaises(ValidationError):
            StudentSubject.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
                is_specialty=False,
            )

    def test_subject_cannot_be_switched_to_individual_with_group_assignments(self):
        data = self.create_base_journal()
        subject = data['solfeggio']
        subject.is_specialty = True

        with self.assertRaises(ValidationError):
            subject.save()

    def test_subject_cannot_be_switched_to_group_with_individual_assignments(self):
        data = self.create_base_journal()
        subject = data['specialty']
        subject.is_specialty = False

        with self.assertRaises(ValidationError):
            subject.save()

    def test_student_can_have_only_one_active_specialty(self):
        data = self.create_base_journal()
        another_specialty = self.create_subject(
            name='Специальность 2',
            is_specialty=True,
        )

        with self.assertRaises(ValidationError):
            StudentSubject.objects.create(
                student=data['student'],
                subject=another_specialty,
                teacher=data['teacher'],
                is_specialty=True,
                is_active=True,
            )

    def test_teacher_subject_stores_qualification_not_assignment(self):
        subject = self.create_subject()
        teacher = self.create_teacher()

        TeacherSubject.objects.create(teacher=teacher, subject=subject)

        self.assertIn(subject, teacher.qualified_subjects.all())
        self.assertEqual(teacher.group_subjects.count(), 0)

    def test_group_subject_teacher_change_syncs_teacher_subjects_and_grades(self):
        data = self.create_base_journal()
        assignment = GroupSubject.objects.get(
            group=data['group'],
            subject=data['solfeggio'],
        )
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            date=date(2025, 10, 8),
            value='5',
        )

        assignment.teacher = data['other_teacher']
        assignment.save()

        grade.refresh_from_db()
        self.assertEqual(grade.teacher, data['other_teacher'])
        self.assertTrue(
            TeacherSubject.objects.filter(
                teacher=data['other_teacher'],
                subject=data['solfeggio'],
            ).exists(),
        )
        self.assertFalse(
            TeacherSubject.objects.filter(
                teacher=data['teacher'],
                subject=data['solfeggio'],
            ).exists(),
        )

    def test_individual_subject_teacher_change_syncs_teacher_subjects_and_grades(self):
        data = self.create_base_journal()
        assignment = StudentSubject.objects.get(
            student=data['student'],
            subject=data['specialty'],
        )
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['specialty'],
            teacher=data['other_teacher'],
            date=date(2025, 10, 9),
            value='4',
        )

        assignment.teacher = data['teacher']
        assignment.save()

        grade.refresh_from_db()
        self.assertEqual(grade.teacher, data['teacher'])
        self.assertTrue(
            TeacherSubject.objects.filter(
                teacher=data['teacher'],
                subject=data['specialty'],
            ).exists(),
        )
        self.assertFalse(
            TeacherSubject.objects.filter(
                teacher=data['other_teacher'],
                subject=data['specialty'],
            ).exists(),
        )


class GradeModelTests(JournalTestDataMixin, TestCase):
    def test_group_subject_teacher_can_create_grade(self):
        data = self.create_base_journal()

        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            date=date(2025, 10, 1),
            value='5',
        )

        self.assertEqual(grade.academic_year, data['year'])
        self.assertTrue(grade.is_group_subject)
        self.assertFalse(grade.is_individual_subject)

    def test_individual_subject_teacher_can_create_grade(self):
        data = self.create_base_journal()

        grade = Grade.objects.create(
            student=data['student'],
            subject=data['specialty'],
            teacher=data['other_teacher'],
            date=date(2025, 10, 2),
            value='4',
        )

        self.assertTrue(grade.is_individual_subject)
        self.assertFalse(grade.is_group_subject)

    def test_unassigned_teacher_cannot_create_grade(self):
        data = self.create_base_journal()

        with self.assertRaises(ValidationError):
            Grade.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['other_teacher'],
                date=date(2025, 10, 3),
                value='5',
            )

    def test_student_cannot_receive_grade_for_unassigned_subject(self):
        data = self.create_base_journal()
        unassigned_subject = self.create_subject(name='Хор')

        with self.assertRaises(ValidationError):
            Grade.objects.create(
                student=data['student'],
                subject=unassigned_subject,
                teacher=data['teacher'],
                date=date(2025, 10, 4),
                value='5',
            )

    def test_duplicate_grade_for_same_student_subject_and_date_is_rejected(self):
        data = self.create_base_journal()

        Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            date=date(2025, 10, 5),
            value='4',
        )

        with self.assertRaises(ValidationError):
            Grade.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
                date=date(2025, 10, 5),
                value='5',
            )

    def test_grade_value_is_normalized_and_limited(self):
        data = self.create_base_journal()

        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            date=date(2025, 10, 6),
            value='н',
        )

        self.assertEqual(grade.value, 'Н')

        with self.assertRaises(ValidationError):
            Grade.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
                date=date(2025, 10, 7),
                value='6',
            )


class SubjectResultModelTests(JournalTestDataMixin, TestCase):
    def test_subject_result_is_unique_for_student_subject_and_year(self):
        data = self.create_base_journal()

        SubjectResult.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            academic_year=data['year'],
            exam_grade='5',
            final_grade='5',
        )

        with self.assertRaises(ValidationError):
            SubjectResult.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                academic_year=data['year'],
                exam_grade='4',
                final_grade='4',
            )

    def test_subject_result_rejects_unassigned_subject(self):
        data = self.create_base_journal()
        unassigned_subject = self.create_subject(name='Хор')

        with self.assertRaises(ValidationError):
            SubjectResult.objects.create(
                student=data['student'],
                subject=unassigned_subject,
                academic_year=data['year'],
                final_grade='5',
            )

    def test_pass_fail_result_accepts_only_pass_fail_values(self):
        data = self.create_base_journal()
        pass_fail_subject = self.create_subject(
            name='Зачетный предмет',
            final_grade_type=Subject.FINAL_GRADE_TYPE_PASS_FAIL,
        )

        GroupSubject.objects.create(
            group=data['group'],
            subject=pass_fail_subject,
            teacher=data['teacher'],
        )

        result = SubjectResult.objects.create(
            student=data['student'],
            subject=pass_fail_subject,
            academic_year=data['year'],
            exam_grade='зачет',
            final_grade='незачет',
        )

        self.assertEqual(result.exam_grade, 'Зачет')
        self.assertEqual(result.final_grade, 'Незачет')

        with self.assertRaises(ValidationError):
            SubjectResult.objects.create(
                student=data['student'],
                subject=pass_fail_subject,
                academic_year=data['year'],
                exam_grade='5',
                final_grade='5',
            )


class CourseApplicationLifecycleTests(JournalTestDataMixin, TestCase):
    def test_default_status_is_confirmed_and_creates_journal_records(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        application.refresh_from_db()
        credential = TemporaryCredential.objects.get(
            course_application=application,
        )
        student = Student.objects.get(pk=application.student_id)
        user = User.objects.get(pk=application.user_id)

        self.assertEqual(application.status, CourseApplication.STATUS_CONFIRMED)
        self.assertEqual(application.generated_login, 'Иванов Иван')
        self.assertEqual(credential.login, 'Иванов Иван')
        self.assertEqual(credential.temporary_password, 'Temp12345!')
        self.assertEqual(credential.student_phone, '+7 (999) 123-45-67')
        self.assertEqual(credential.user, user)
        self.assertTrue(user.check_password('Temp12345!'))
        self.assertEqual(student.full_name, 'Иванов Иван Иванович')
        self.assertEqual(student.gender, application.gender)
        self.assertEqual(student.birth_date, application.birth_date)
        self.assertEqual(student.city_church, application.city_church)
        self.assertEqual(student.music_education, application.music_education)
        self.assertEqual(student.student_phone, application.student_phone)
        self.assertEqual(student.parent_contacts, application.parent_contacts)
        self.assertEqual(student.comments, application.comments)
        self.assertEqual(student.user, user)
        self.assertEqual(student.group.name, CourseApplication.STUDENT_COURSE_GROUP_NAME)
        self.assertEqual(student.instrument.name, 'Баян I')
        self.assertIsNotNone(application.journal_created_at)
        self.assertIsNone(application.journal_removed_at)

    def test_confirmed_application_updates_existing_student_profile_details(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        application.birth_date = date(1999, 5, 4)
        application.city_church = 'Воронеж / Отрожка'
        application.instrument = 'Фортепиано'
        application.music_education = CourseApplication.MUSIC_EDUCATION_HIGHER
        application.student_phone = '+7 (999) 123-45-69'
        application.parent_contacts = 'Отец - +7 (999) 111-22-33'
        application.comments = 'Нужен вечерний поток'
        application.save()

        student = application.student
        student.refresh_from_db()
        credential = TemporaryCredential.objects.get(course_application=application)

        self.assertEqual(student.birth_date, date(1999, 5, 4))
        self.assertEqual(student.city_church, 'Воронеж / Отрожка')
        self.assertEqual(student.instrument.name, 'Фортепиано')
        self.assertEqual(student.music_education, CourseApplication.MUSIC_EDUCATION_HIGHER)
        self.assertEqual(student.student_phone, '+7 (999) 123-45-69')
        self.assertEqual(student.parent_contacts, 'Отец - +7 (999) 111-22-33')
        self.assertEqual(student.comments, 'Нужен вечерний поток')
        self.assertEqual(credential.student_phone, '+7 (999) 123-45-69')

    def test_confirmed_application_update_preserves_password_and_temporary_password(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        user = application.user
        credential = TemporaryCredential.objects.get(course_application=application)
        original_password_hash = user.password
        original_temporary_password = credential.temporary_password

        application.first_name = 'Пётр'
        application.comments = 'Данные заявки изменены'
        application.save()

        user.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(user.password, original_password_hash)
        self.assertEqual(credential.temporary_password, original_temporary_password)
        self.assertTrue(user.check_password(original_temporary_password))

    def test_same_person_in_later_year_reuses_student_and_user(self):
        first_year = self.create_academic_year(name='2025/2026')
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            first_application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        original_student_id = first_application.student_id
        original_user_id = first_application.user_id
        original_password_hash = first_application.user.password

        second_year = self.create_academic_year(name='2026/2027')
        second_application = CourseApplication.objects.create(
            **self.application_payload(
                student_phone='+7 (999) 765-43-21',
                city_church='Новый город / Новая церковь',
            ),
        )

        second_application.refresh_from_db()
        second_application.user.refresh_from_db()
        self.assertEqual(second_application.student_id, original_student_id)
        self.assertEqual(second_application.user_id, original_user_id)
        self.assertEqual(second_application.user.password, original_password_hash)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(User.objects.filter(pk=original_user_id).count(), 1)
        self.assertEqual(TemporaryCredential.objects.count(), 1)
        self.assertTrue(
            StudentEnrollment.objects.filter(
                student_id=original_student_id,
                academic_year=first_year,
            ).exists(),
        )
        self.assertTrue(
            StudentEnrollment.objects.filter(
                student_id=original_student_id,
                academic_year=second_year,
            ).exists(),
        )

    def test_different_birth_date_creates_different_student_even_with_same_name(self):
        self.create_academic_year(name='2025/2026')
        first_application = CourseApplication.objects.create(**self.application_payload())
        self.create_academic_year(name='2026/2027')
        second_application = CourseApplication.objects.create(
            **self.application_payload(
                birth_date=date(2001, 1, 1),
                student_phone='+7 (999) 765-43-21',
            ),
        )

        self.assertNotEqual(first_application.student_id, second_application.student_id)
        self.assertEqual(Student.objects.count(), 2)

    def test_confirmed_applications_add_suffix_for_duplicate_login(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            first = CourseApplication.objects.create(
                **self.application_payload(),
            )
            second = CourseApplication.objects.create(
                **self.application_payload(
                    birth_date=date(2001, 1, 1),
                    student_phone='+7 (999) 123-45-68',
                ),
            )

        self.assertEqual(first.generated_login, 'Иванов Иван')
        self.assertEqual(second.generated_login, 'Иванов Иван 2')
        self.assertEqual(
            list(
                TemporaryCredential.objects.order_by('id').values_list(
                    'login',
                    flat=True,
                ),
            ),
            ['Иванов Иван', 'Иванов Иван 2'],
        )

    def test_rejecting_one_of_two_confirmed_applications_preserves_shared_account(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            first = CourseApplication.objects.create(**self.application_payload())
        second = CourseApplication.objects.create(
            **self.application_payload(student_phone='+7 (999) 765-43-21'),
        )
        student_id = first.student_id
        user_id = first.user_id
        enrollment_id = StudentEnrollment.objects.get(
            student_id=student_id,
            academic_year=first.academic_year,
        ).pk

        first.status = CourseApplication.STATUS_REJECTED
        first.save()
        first.refresh_from_db()
        second.refresh_from_db()
        credential = TemporaryCredential.objects.get(user_id=user_id)

        self.assertIsNone(first.student_id)
        self.assertIsNone(first.user_id)
        self.assertEqual(second.student_id, student_id)
        self.assertEqual(second.user_id, user_id)
        self.assertTrue(Student.objects.filter(pk=student_id).exists())
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        self.assertTrue(StudentEnrollment.objects.filter(pk=enrollment_id).exists())
        self.assertEqual(credential.course_application, second)
        self.assertEqual(credential.temporary_password, 'Temp12345!')

    def test_deleting_one_of_two_confirmed_applications_preserves_shared_account(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            first = CourseApplication.objects.create(**self.application_payload())
        second = CourseApplication.objects.create(
            **self.application_payload(student_phone='+7 (999) 765-43-21'),
        )
        student_id = first.student_id
        user_id = first.user_id

        first.delete()
        second.refresh_from_db()
        credential = TemporaryCredential.objects.get(user_id=user_id)

        self.assertEqual(second.student_id, student_id)
        self.assertEqual(second.user_id, user_id)
        self.assertTrue(Student.objects.filter(pk=student_id).exists())
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        self.assertTrue(
            StudentEnrollment.objects.filter(
                student_id=student_id,
                academic_year=second.academic_year,
            ).exists(),
        )
        self.assertEqual(credential.course_application, second)

    def test_rejected_application_does_not_create_journal_records(self):
        application = CourseApplication.objects.create(
            **self.application_payload(status=CourseApplication.STATUS_REJECTED),
        )

        application.refresh_from_db()

        self.assertEqual(application.status, CourseApplication.STATUS_REJECTED)
        self.assertIsNone(application.student)
        self.assertIsNone(application.user)
        self.assertEqual(Student.objects.count(), 0)
        self.assertEqual(TemporaryCredential.objects.count(), 0)

    def test_changing_status_to_rejected_removes_student_user_and_temporary_credentials(
        self,
    ):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        login = application.generated_login
        student_id = application.student_id
        user_id = application.user_id

        self.assertTrue(Student.objects.filter(pk=student_id).exists())
        self.assertTrue(User.objects.filter(pk=user_id).exists())
        self.assertTrue(TemporaryCredential.objects.filter(login=login).exists())

        application.status = CourseApplication.STATUS_REJECTED
        application.save()
        application.refresh_from_db()

        self.assertEqual(CourseApplication.objects.count(), 1)
        self.assertEqual(application.status, CourseApplication.STATUS_REJECTED)
        self.assertEqual(application.generated_login, login)
        self.assertIsNone(application.student)
        self.assertIsNone(application.user)
        self.assertIsNotNone(application.journal_removed_at)
        self.assertFalse(Student.objects.filter(pk=student_id).exists())
        self.assertFalse(User.objects.filter(pk=user_id).exists())
        self.assertFalse(TemporaryCredential.objects.filter(login=login).exists())

    def test_changing_rejected_application_back_to_confirmed_recreates_records(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        application.status = CourseApplication.STATUS_REJECTED
        application.save()
        application.refresh_from_db()

        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='NewTemp12345!',
        ):
            application.status = CourseApplication.STATUS_CONFIRMED
            application.save()

        application.refresh_from_db()
        credential = TemporaryCredential.objects.get(
            course_application=application,
        )

        self.assertEqual(application.status, CourseApplication.STATUS_CONFIRMED)
        self.assertIsNotNone(application.student)
        self.assertIsNotNone(application.user)
        self.assertEqual(application.generated_login, 'Иванов Иван')
        self.assertEqual(credential.login, 'Иванов Иван')
        self.assertEqual(credential.temporary_password, 'NewTemp12345!')
        self.assertTrue(application.user.check_password('NewTemp12345!'))
        self.assertIsNone(application.journal_removed_at)

    def test_deleting_application_removes_created_journal_records(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            application = CourseApplication.objects.create(
                **self.application_payload(),
            )

        user_id = application.user_id
        student_id = application.student_id

        application.delete()

        self.assertEqual(CourseApplication.objects.count(), 0)
        self.assertFalse(Student.objects.filter(pk=student_id).exists())
        self.assertFalse(User.objects.filter(pk=user_id).exists())
        self.assertEqual(TemporaryCredential.objects.count(), 0)

    def test_duplicate_student_phone_is_rejected(self):
        CourseApplication.objects.create(**self.application_payload())

        with self.assertRaises(ValidationError):
            CourseApplication.objects.create(
                **self.application_payload(
                    last_name='Петров',
                    first_name='Пётр',
                    middle_name='Петрович',
                    student_phone='8 999 123 45 67',
                ),
            )

    def test_editing_confirmed_application_does_not_reset_existing_user_password(self):
        application = CourseApplication.objects.create(**self.application_payload())
        user = application.user
        original_password_hash = user.password
        TemporaryCredential.objects.filter(course_application=application).delete()

        application.comments = 'Обновленный комментарий'
        application.save()
        user.refresh_from_db()

        self.assertEqual(user.password, original_password_hash)
        self.assertFalse(
            TemporaryCredential.objects.filter(course_application=application).exists(),
        )


class FormTests(JournalTestDataMixin, TestCase):
    def test_public_course_application_form_hides_status_and_normalizes_phone(self):
        form = CourseApplicationPublicForm(
            data=self.application_form_payload(
                student_phone='8 999 123 45 67',
            ),
        )

        self.assertNotIn('status', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['student_phone'], '+7 (999) 123-45-67')

    def test_admin_course_application_form_includes_status(self):
        form = CourseApplicationAdminForm(
            data=self.application_form_payload(
                status=CourseApplication.STATUS_REJECTED,
            ),
        )

        self.assertIn('status', form.fields)
        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['status'], CourseApplication.STATUS_REJECTED)

    def test_course_application_edit_form_keeps_existing_field_values(self):
        application = CourseApplication(
            **self.application_payload(
                birth_date=date(2000, 1, 2),
                city_church='Воронеж, Отрожка',
                instrument='Фортепиано',
                music_education=CourseApplication.MUSIC_EDUCATION_BASIC,
                comments='Нужен вечерний поток',
            ),
        )

        form = CourseApplicationAdminForm(instance=application)

        self.assertIn('value="Иванов"', str(form['last_name']))
        self.assertIn('value="2000-01-02"', str(form['birth_date']))
        self.assertIn('Воронеж, Отрожка', str(form['city_church']))
        self.assertIn('Фортепиано', str(form['instrument']))
        self.assertIn('value="basic" selected', str(form['music_education']))
        self.assertIn('Нужен вечерний поток', str(form['comments']))

    def test_course_application_form_uses_instrument_directory_when_available(self):
        self.create_instrument(name='Баян')
        self.create_instrument(name='Фортепиано')

        valid_form = CourseApplicationPublicForm(
            data=self.application_form_payload(instrument='Баян'),
        )
        invalid_form = CourseApplicationPublicForm(
            data=self.application_form_payload(instrument='Случайный инструмент'),
        )

        self.assertIn('<select', str(valid_form['instrument']))
        self.assertTrue(valid_form.is_valid(), valid_form.errors)
        self.assertFalse(invalid_form.is_valid())
        self.assertIn('instrument', invalid_form.errors)

    def test_parent_contacts_accepts_dash_from_form_placeholder(self):
        normalized_contacts = normalize_parent_contacts(
            'Иванов Иван Иванович — +7 (999) 123-45-67',
        )

        self.assertEqual(
            normalized_contacts,
            'Иванов Иван Иванович - +7 (999) 123-45-67',
        )

    def test_minimum_birth_date_for_age_handles_leap_course_start_date(self):
        self.assertEqual(
            minimum_birth_date_for_age(14, today=date(2024, 2, 29)),
            date(2010, 2, 28),
        )

    def test_public_course_application_form_enforces_age_limit(self):
        too_young_birth_date = date.today().replace(
            year=date.today().year - 10,
        ).isoformat()

        form = CourseApplicationPublicForm(
            data=self.application_form_payload(
                birth_date=too_young_birth_date,
            ),
        )

        self.assertFalse(form.is_valid())
        self.assertIn('birth_date', form.errors)

    def test_public_course_application_form_uses_registration_settings_age_and_course_start(self):
        self.create_academic_year(name='2025/2026')
        registration_settings = CourseRegistrationSettings.objects.create(
            pk=1,
            telegram_group_url='https://t.me/test_group',
            minimum_registration_age=15,
        )

        too_young_form = CourseApplicationPublicForm(
            data=self.application_form_payload(
                birth_date=date(2010, 9, 2),
            ),
            registration_settings=registration_settings,
        )
        allowed_form = CourseApplicationPublicForm(
            data=self.application_form_payload(
                birth_date=date(2010, 9, 1),
            ),
            registration_settings=registration_settings,
        )

        self.assertFalse(too_young_form.is_valid())
        self.assertIn('birth_date', too_young_form.errors)
        self.assertTrue(allowed_form.is_valid(), allowed_form.errors)
        self.assertEqual(
            allowed_form.fields['birth_date'].widget.attrs['max'],
            '2010-09-01',
        )
        self.assertEqual(
            allowed_form.fields['birth_date'].widget.attrs['data-age-limit'],
            '15',
        )
        self.assertEqual(
            allowed_form.fields['birth_date'].widget.attrs['data-age-reference-date'],
            '2025-09-01',
        )

    def test_course_registration_settings_form_stores_age_and_uses_active_year_dates(self):
        academic_year = self.create_academic_year(name='2026/2027')
        form = CourseRegistrationSettingsForm(
            instance=CourseRegistrationSettings.load(),
            data={
                'telegram_group_url': ' https://t.me/test_group ',
                'minimum_registration_age': 16,
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        settings_obj = form.save()
        self.assertEqual(settings_obj.telegram_group_url, 'https://t.me/test_group')
        self.assertEqual(settings_obj.minimum_registration_age, 16)
        self.assertFalse(hasattr(settings_obj, 'course_starts_on'))
        self.assertFalse(hasattr(settings_obj, 'course_ends_on'))
        self.assertEqual(AcademicYear.get_active(), academic_year)

    def test_course_registration_settings_form_does_not_accept_course_dates(self):
        form = CourseRegistrationSettingsForm(
            data={
                'telegram_group_url': 'https://t.me/test_group',
                'minimum_registration_age': 14,
                'course_starts_on': '2026-08-31',
                'course_ends_on': '2025-09-01',
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertNotIn('course_starts_on', form.fields)
        self.assertNotIn('course_ends_on', form.fields)

    def test_grade_form_accepts_only_assigned_teacher_for_student_subject(self):
        data = self.create_base_journal()

        form = GradeCreateForm(
            data={
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'teacher': data['teacher'].pk,
                'academic_year': data['year'].pk,
                'date': '2025-10-10',
                'value': '5',
                'comment': '',
            },
        )

        self.assertTrue(form.is_valid(), form.errors)

        invalid_form = GradeCreateForm(
            data={
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'teacher': data['other_teacher'].pk,
                'academic_year': data['year'].pk,
                'date': '2025-10-10',
                'value': '5',
                'comment': '',
            },
        )

        self.assertFalse(invalid_form.is_valid())
        self.assertIn(
            'Этот преподаватель не назначен выбранному ученику',
            str(invalid_form.errors),
        )

    def test_grade_form_with_fixed_teacher_removes_teacher_field(self):
        data = self.create_base_journal()

        form = GradeCreateForm(
            teacher=data['teacher'],
            group=data['group'],
            subject=data['solfeggio'],
            academic_year=data['year'],
        )

        self.assertNotIn('teacher', form.fields)
        self.assertNotIn('subject', form.fields)
        self.assertEqual(list(form.fields['student'].queryset), [data['student']])

    def test_grade_form_with_fixed_teacher_reports_invalid_subject_without_crash(self):
        data = self.create_base_journal()

        form = GradeCreateForm(
            data={
                'student': data['student'].pk,
                'subject': data['literature'].pk,
                'academic_year': data['year'].pk,
                'date': '2025-10-10',
                'value': '5',
                'comment': '',
            },
            teacher=data['teacher'],
            group=data['group'],
            academic_year=data['year'],
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            'Этот преподаватель не назначен выбранному ученику',
            str(form.errors),
        )

    def test_grade_form_rejects_student_from_another_group(self):
        data = self.create_base_journal()
        another_group = self.create_group(
            name='Другая группа',
            academic_year=data['year'],
        )

        form = GradeCreateForm(
            data={
                'group': another_group.pk,
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'teacher': data['teacher'].pk,
                'academic_year': data['year'].pk,
                'date': '2025-10-10',
                'value': '5',
                'comment': '',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Выбранный ученик недоступен', str(form.errors))

    def test_grade_form_rejects_inactive_student_from_forged_post(self):
        data = self.create_base_journal()
        data['student'].is_active = False
        data['student'].save()

        form = GradeCreateForm(
            data={
                'group': data['group'].pk,
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'teacher': data['teacher'].pk,
                'academic_year': data['year'].pk,
                'date': '2025-10-10',
                'value': '5',
                'comment': '',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Выбранный ученик недоступен', str(form.errors))

    def test_grade_form_rejects_group_from_another_academic_year(self):
        data = self.create_base_journal()
        another_year = AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
            is_active=False,
        )

        form = GradeCreateForm(
            data={
                'group': data['group'].pk,
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'teacher': data['teacher'].pk,
                'academic_year': another_year.pk,
                'date': '2026-10-10',
                'value': '5',
                'comment': '',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Группа относится к другому учебному году.', str(form.errors))

    def test_grade_admin_form_limits_related_fields_and_loads_dependency_script(self):
        data = self.create_base_journal()
        form = GradeAdminForm(
            data={
                'group': data['group'].pk,
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'academic_year': data['year'].pk,
            },
        )

        self.assertEqual(list(form.fields['teacher'].queryset), [data['teacher']])
        self.assertEqual(list(form.fields['student'].queryset), [data['student']])
        self.assertIn('journal/grade_dependencies.js', GradeAdmin.Media.js)

    def test_grade_edit_form_keeps_existing_date_value(self):
        form = GradeAdminForm(instance=Grade(date=date(2025, 10, 10), value='5'))

        self.assertIn('value="2025-10-10"', str(form['date']))

    def test_student_and_teacher_edit_forms_keep_existing_birth_dates(self):
        data = self.create_base_journal()
        data['student'].birth_date = date(2010, 3, 2)
        data['teacher'].birth_date = date(1980, 4, 3)

        student_form = StudentAdminForm(instance=data['student'])
        teacher_form = TeacherAdminForm(instance=data['teacher'])

        self.assertIn('value="2010-03-02"', str(student_form['birth_date']))
        self.assertIn('value="1980-04-03"', str(teacher_form['birth_date']))

    def test_subject_result_form_validates_allowed_subject_and_grade_type(self):
        data = self.create_base_journal()

        form = SubjectResultForm(
            data={
                'student': data['student'].pk,
                'subject': data['solfeggio'].pk,
                'academic_year': data['year'].pk,
                'exam_grade': '5',
                'final_grade': '4',
            },
        )

        self.assertTrue(form.is_valid(), form.errors)

        pass_fail_subject = self.create_subject(
            name='Зачетный предмет',
            final_grade_type=Subject.FINAL_GRADE_TYPE_PASS_FAIL,
        )

        GroupSubject.objects.create(
            group=data['group'],
            subject=pass_fail_subject,
            teacher=data['teacher'],
        )

        invalid_form = SubjectResultForm(
            data={
                'student': data['student'].pk,
                'subject': pass_fail_subject.pk,
                'academic_year': data['year'].pk,
                'exam_grade': '5',
                'final_grade': '5',
            },
        )

        self.assertFalse(invalid_form.is_valid())
        self.assertIn('Недопустимое значение', str(invalid_form.errors))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_detailed_password_change_form_has_no_old_password_field_and_saves_new_password(
        self,
    ):
        user = User.objects.create_user(
            username='password_user',
            password='OldPass12345!',
        )

        form = DetailedPasswordChangeForm(
            user,
            data={
                'new_password1': 'NewPass12345!',
                'new_password2': 'NewPass12345!',
            },
        )

        self.assertNotIn('old_password', form.fields)
        self.assertTrue(form.is_valid(), form.errors)

        form.save()
        user.refresh_from_db()

        self.assertTrue(user.check_password('NewPass12345!'))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_detailed_password_change_form_rejects_unchanged_password(self):
        user = User.objects.create_user(
            username='password_user',
            password='SamePass12345!',
        )

        form = DetailedPasswordChangeForm(
            user,
            data={
                'new_password1': 'SamePass12345!',
                'new_password2': 'SamePass12345!',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn(
            'Новый пароль не должен совпадать со старым.',
            str(form.errors),
        )


class SelectorHelperTests(JournalTestDataMixin, TestCase):
    def test_helper_functions_return_only_real_assignments(self):
        data = self.create_base_journal()

        allowed_subjects = get_student_allowed_subjects(data['student'])

        self.assertIn(data['solfeggio'], allowed_subjects)
        self.assertIn(data['literature'], allowed_subjects)
        self.assertIn(data['specialty'], allowed_subjects)

        solfeggio_teachers = get_student_subject_teachers(
            data['student'],
            data['solfeggio'],
        )
        specialty_teachers = get_student_subject_teachers(
            data['student'],
            data['specialty'],
        )

        self.assertEqual(list(solfeggio_teachers), [data['teacher']])
        self.assertEqual(list(specialty_teachers), [data['other_teacher']])

        self.assertIn(data['group'], get_teacher_groups(data['teacher']))
        self.assertIn(data['solfeggio'], get_teacher_subjects(data['teacher']))
        self.assertNotIn(data['specialty'], get_teacher_subjects(data['teacher']))

    def test_grade_option_helpers_keep_only_complete_active_assignments(self):
        data = self.create_base_journal()

        self.assertEqual(
            list(get_grade_groups(teacher=data['teacher'])),
            [data['group']],
        )
        self.assertEqual(
            list(get_grade_students(
                group=data['group'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
            )),
            [data['student']],
        )
        self.assertFalse(
            get_grade_students(
                group=data['group'],
                subject=data['solfeggio'],
                teacher=data['other_teacher'],
            ).exists(),
        )
        self.assertEqual(
            list(get_grade_subjects(
                group=data['group'],
                student=data['student'],
                teacher=data['teacher'],
            )),
            [data['solfeggio']],
        )
        self.assertEqual(
            list(get_grade_teachers(
                group=data['group'],
                student=data['student'],
                subject=data['specialty'],
            )),
            [data['other_teacher']],
        )

    def test_grade_option_helpers_hide_archived_year_by_default_but_allow_explicit_view(self):
        data = self.create_base_journal()
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )

        self.assertNotIn(data['group'], get_grade_groups(teacher=data['teacher']))
        self.assertIn(
            data['group'],
            get_grade_groups(teacher=data['teacher'], academic_year=data['year']),
        )


class GradeOptionsApiTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.data = self.create_base_journal()
        self.admin_user = User.objects.create_superuser(
            username='grade_options_admin',
            password='Pass12345!',
        )

    def test_admin_options_narrow_teachers_for_selected_assignment(self):
        self.client.login(username='grade_options_admin', password='Pass12345!')

        response = self.client.get(
            reverse('grade_options_api'),
            {
                'group': self.data['group'].pk,
                'student': self.data['student'].pk,
                'subject': self.data['solfeggio'].pk,
                'academic_year': self.data['year'].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            payload['teachers'],
            [{'id': self.data['teacher'].pk, 'label': self.data['teacher'].full_name}],
        )
        self.assertEqual(
            [item['id'] for item in payload['students']],
            [self.data['student'].pk],
        )
        self.assertEqual(payload['defaults']['group_id'], self.data['group'].pk)
        self.assertEqual(payload['defaults']['academic_year_id'], self.data['year'].pk)

    def test_options_keep_currently_selected_values_when_other_field_changes(self):
        self.client.login(username='grade_options_admin', password='Pass12345!')

        response = self.client.get(
            reverse('grade_options_api'),
            {
                'group': self.data['group'].pk,
                'student': self.data['student'].pk,
                'subject': self.data['solfeggio'].pk,
                'teacher': self.data['other_teacher'].pk,
                'academic_year': self.data['year'].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(self.data['group'].pk, [item['id'] for item in payload['groups']])
        self.assertIn(self.data['student'].pk, [item['id'] for item in payload['students']])
        self.assertIn(self.data['solfeggio'].pk, [item['id'] for item in payload['subjects']])
        self.assertIn(self.data['other_teacher'].pk, [item['id'] for item in payload['teachers']])

    def test_strict_options_drop_incompatible_dependent_values(self):
        other_year = self.create_academic_year(name='2026/2027')
        other_group = self.create_group(name='Другая группа', academic_year=other_year)
        self.client.login(username='grade_options_admin', password='Pass12345!')

        response = self.client.get(
            reverse('grade_options_api'),
            {
                'group': other_group.pk,
                'student': self.data['student'].pk,
                'subject': self.data['solfeggio'].pk,
                'teacher': self.data['teacher'].pk,
                'changed': 'group',
                'strict': '1',
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(other_group.pk, [item['id'] for item in payload['groups']])
        self.assertNotIn(self.data['student'].pk, [item['id'] for item in payload['students']])
        self.assertNotIn(self.data['solfeggio'].pk, [item['id'] for item in payload['subjects']])
        self.assertNotIn(self.data['teacher'].pk, [item['id'] for item in payload['teachers']])

    def test_teacher_options_are_always_limited_to_own_assignments(self):
        self.client.login(username='teacher_ivanov', password='Pass12345!')

        response = self.client.get(reverse('grade_options_api'))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(
            [item['id'] for item in payload['teachers']],
            [self.data['teacher'].pk],
        )
        self.assertIn(
            self.data['solfeggio'].pk,
            [item['id'] for item in payload['subjects']],
        )
        self.assertNotIn(
            self.data['literature'].pk,
            [item['id'] for item in payload['subjects']],
        )
        self.assertNotIn(
            self.data['specialty'].pk,
            [item['id'] for item in payload['subjects']],
        )

    def test_student_cannot_request_grade_entry_options(self):
        self.client.login(username='student_sidorov', password='Pass12345!')

        response = self.client.get(reverse('grade_options_api'))

        self.assertEqual(response.status_code, 403)


class AssignmentOptionsApiTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.data = self.create_base_journal()
        self.admin_user = User.objects.create_superuser(
            username='assignment_options_admin',
            password='Pass12345!',
        )

    def test_student_subject_options_return_defaults_for_selected_subject(self):
        extra_subject = self.create_subject(
            name='Индивидуальная импровизация',
            is_specialty=True,
        )
        self.client.login(username='assignment_options_admin', password='Pass12345!')

        response = self.client.get(
            reverse('assignment_options_api'),
            {
                'type': 'student_subject',
                'student': self.data['student'].pk,
                'subject': extra_subject.pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload['defaults']['is_specialty'])
        self.assertEqual(payload['defaults']['group_id'], self.data['group'].pk)
        self.assertEqual(payload['defaults']['academic_year_id'], self.data['year'].pk)
        self.assertIn(extra_subject.pk, [item['id'] for item in payload['subjects']])

    def test_group_subject_options_return_next_sort_order_and_group_year(self):
        self.client.login(username='assignment_options_admin', password='Pass12345!')

        response = self.client.get(
            reverse('assignment_options_api'),
            {
                'type': 'group_subject',
                'group': self.data['group'].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload['defaults']['academic_year_id'], self.data['year'].pk)
        self.assertEqual(payload['defaults']['sort_order'], 110)
        self.assertIn(self.data['group'].pk, [item['id'] for item in payload['groups']])


class ViewTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.data = self.create_base_journal()
        self.admin_user = User.objects.create_superuser(
            username='admin_test',
            password='Pass12345!',
            email='admin@example.com',
        )

    def test_teacher_can_open_journal_only_with_assigned_data(self):
        self.client.login(username='teacher_ivanov', password='Pass12345!')

        response = self.client.get(
            reverse('journal'),
            {'group': self.data['group'].pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Сольфеджио')
        self.assertNotContains(response, 'Регистрация на курсы')

    def test_student_without_group_can_open_journal(self):
        self.data['student'].group = None
        self.data['student'].save(update_fields=['group'])
        self.client.login(username='student_sidorov', password='Pass12345!')

        response = self.client.get(reverse('journal'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Мои оценки')
        self.assertContains(response, 'Нет данных по выбранным фильтрам.')

    def test_user_with_temporary_password_is_redirected_to_password_change(self):
        TemporaryCredential.objects.create(
            user=self.data['teacher'].user,
            login=self.data['teacher'].user.username,
            temporary_password='abc234de',
        )
        self.data['teacher'].user.username = 'teacher_renamed'
        self.data['teacher'].user.save(update_fields=['username'])
        self.client.login(username='teacher_ivanov', password='Pass12345!')
        self.client.force_login(self.data['teacher'].user)

        response = self.client.get(reverse('journal'))

        self.assertRedirects(response, reverse('password_change'))

    def test_academic_year_filter_limits_admin_groups(self):
        next_year = AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
            is_active=False,
        )
        next_group = StudyGroup.objects.create(
            name='Группа следующего года',
            academic_year=next_year,
        )
        next_subject = self.create_subject(name='Предмет следующего года')
        next_student = self.create_student(
            full_name='Ученик Следующего Года',
            group=next_group,
            instrument=self.data['instrument'],
            username='student_next_year',
        )
        GroupSubject.objects.create(
            group=next_group,
            subject=next_subject,
            teacher=self.data['teacher'],
        )
        Grade.objects.create(
            student=next_student,
            subject=next_subject,
            teacher=self.data['teacher'],
            academic_year=next_year,
            date=date(2026, 10, 1),
            value='5',
        )
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.get(
            reverse('journal'),
            {'academic_year': self.data['year'].pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.data['group'].name)
        self.assertNotContains(response, next_group.name)

    def test_inline_edit_rolls_back_when_later_value_is_invalid(self):
        grade = Grade.objects.create(
            student=self.data['student'],
            subject=self.data['solfeggio'],
            teacher=self.data['teacher'],
            academic_year=self.data['year'],
            date=date(2025, 10, 15),
            value='5',
        )
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.post(
            f'{reverse("journal")}?group={self.data["group"].pk}&academic_year={self.data["year"].pk}',
            data={
                'action': 'inline_edit',
                (
                    f'grade__{self.data["solfeggio"].pk}__'
                    f'{self.data["student"].pk}__2025-10-15'
                ): '4',
                f'final__{self.data["solfeggio"].pk}__{self.data["student"].pk}': 'bad',
            },
        )

        self.assertEqual(response.status_code, 200)
        grade.refresh_from_db()
        self.assertEqual(grade.value, '5')

    def test_final_grade_controls_are_visible_without_regular_grades(self):
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.get(
            reverse('journal'),
            {
                'group': self.data['group'].pk,
                'subject': self.data['solfeggio'].pk,
                'academic_year': self.data['year'].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            f'name="final__{self.data["solfeggio"].pk}__{self.data["student"].pk}"',
        )

    def test_archived_academic_year_is_read_only_in_journal(self):
        grade = Grade.objects.create(
            student=self.data['student'],
            subject=self.data['solfeggio'],
            teacher=self.data['teacher'],
            academic_year=self.data['year'],
            date=date(2025, 10, 15),
            value='5',
        )
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.get(
            reverse('journal'),
            {
                'group': self.data['group'].pk,
                'subject': self.data['solfeggio'].pk,
                'academic_year': self.data['year'].pk,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, grade.value)
        self.assertNotContains(
            response,
            (
                f'name="grade__{self.data["solfeggio"].pk}__'
                f'{self.data["student"].pk}__2025-10-15"'
            ),
        )
        self.assertNotContains(response, '<button class="table-save-button"')
        self.assertNotContains(response, 'name="action" value="add_grade"')

    def test_archived_journal_uses_snapshots_after_current_records_are_renamed(self):
        old_student_name = self.data['student'].full_name
        old_subject_name = self.data['solfeggio'].name
        Grade.objects.create(
            student=self.data['student'],
            subject=self.data['solfeggio'],
            teacher=self.data['teacher'],
            academic_year=self.data['year'],
            date=date(2025, 10, 15),
            value='5',
        )
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )
        self.data['student'].refresh_from_db()
        self.data['student'].full_name = 'Текущее имя ученика'
        self.data['student'].save()
        self.data['solfeggio'].name = 'Текущее название предмета'
        self.data['solfeggio'].save()
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.get(
            reverse('journal'),
            {
                'group': self.data['group'].pk,
                'subject': self.data['solfeggio'].pk,
                'academic_year': self.data['year'].pk,
            },
        )

        self.assertContains(response, old_student_name)
        self.assertContains(response, old_subject_name)

    def test_post_to_archived_academic_year_does_not_change_grade(self):
        grade = Grade.objects.create(
            student=self.data['student'],
            subject=self.data['solfeggio'],
            teacher=self.data['teacher'],
            academic_year=self.data['year'],
            date=date(2025, 10, 15),
            value='5',
        )
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.post(
            (
                f'{reverse("journal")}?group={self.data["group"].pk}'
                f'&subject={self.data["solfeggio"].pk}'
                f'&academic_year={self.data["year"].pk}'
            ),
            data={
                'action': 'inline_edit',
                (
                    f'grade__{self.data["solfeggio"].pk}__'
                    f'{self.data["student"].pk}__2025-10-15'
                ): '4',
            },
        )

        self.assertEqual(response.status_code, 302)
        grade.refresh_from_db()
        self.assertEqual(grade.value, '5')

    def test_journal_table_builder_batches_assignment_queries(self):
        second_group = self.create_group(
            name='Вторая группа',
            academic_year=self.data['year'],
        )
        second_student = self.create_student(
            full_name='Ученик Второй Группы',
            group=second_group,
            instrument=self.data['instrument'],
            username='student_second_group',
        )
        extra_subject = self.create_subject(name='Хор')
        GroupSubject.objects.create(
            group=second_group,
            subject=extra_subject,
            teacher=self.data['teacher'],
        )

        groups = [self.data['group'], second_group]
        subjects = [
            self.data['solfeggio'],
            self.data['literature'],
            self.data['specialty'],
            extra_subject,
        ]
        enrollments = StudentEnrollment.objects.filter(
            academic_year=self.data['year'],
            student__in=[self.data['student'], second_student],
        ).select_related('student', 'group')

        with CaptureQueriesContext(connection) as captured_queries:
            journal_tables = _build_journal_tables(
                groups=groups,
                subjects=subjects,
                enrollments=enrollments,
                grade_qs=Grade.objects.none(),
                results_qs=SubjectResult.objects.none(),
                selected_academic_year=self.data['year'],
            )

        self.assertGreaterEqual(len(journal_tables), 4)
        self.assertLessEqual(
            len(captured_queries),
            3,
            [query['sql'] for query in captured_queries],
        )

    def test_student_cannot_edit_inline_grades(self):
        self.client.login(username='student_sidorov', password='Pass12345!')

        response = self.client.post(
            reverse('journal'),
            data={
                'action': 'inline_edit',
                (
                    f'grade__{self.data["solfeggio"].pk}__'
                    f'{self.data["student"].pk}__2025-10-15'
                ): '5',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Grade.objects.exists())

    def test_admin_can_add_grade_by_form(self):
        self.client.login(username='admin_test', password='Pass12345!')

        response = self.client.post(
            f'{reverse("journal")}?group={self.data["group"].pk}',
            data={
                'action': 'add_grade',
                'student': self.data['student'].pk,
                'subject': self.data['solfeggio'].pk,
                'teacher': self.data['teacher'].pk,
                'academic_year': self.data['year'].pk,
                'date': '2025-10-16',
                'value': '5',
                'comment': '',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Grade.objects.filter(
                student=self.data['student'],
                subject=self.data['solfeggio'],
                teacher=self.data['teacher'],
                date=date(2025, 10, 16),
                value='5',
            ).exists(),
        )

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_password_change_view_uses_set_password_form_without_old_password(
        self,
    ):
        TemporaryCredential.objects.create(
            user=self.data['teacher'].user,
            login=self.data['teacher'].user.username,
            temporary_password='abc234de',
        )
        self.data['teacher'].user.username = 'teacher_renamed'
        self.data['teacher'].user.save(update_fields=['username'])
        self.client.login(username='teacher_ivanov', password='Pass12345!')
        self.client.force_login(self.data['teacher'].user)

        get_response = self.client.get(reverse('password_change'))

        self.assertEqual(get_response.status_code, 200)
        self.assertNotContains(get_response, 'name="old_password"')
        self.assertContains(get_response, 'name="new_password1"')
        self.assertContains(get_response, 'name="new_password2"')

        post_response = self.client.post(
            reverse('password_change'),
            data={
                'new_password1': 'NewPass12345!',
                'new_password2': 'NewPass12345!',
            },
        )

        self.assertEqual(post_response.status_code, 302)
        self.assertFalse(
            TemporaryCredential.objects.filter(
                user=self.data['teacher'].user,
            ).exists(),
        )

        self.client.logout()

        self.assertTrue(
            self.client.login(
                username='teacher_renamed',
                password='NewPass12345!',
            ),
        )


class CourseApplicationDuplicateErrorTests(TestCase):
    def test_recognizes_late_model_validation_duplicate(self):
        error = ValidationError({
            'student_phone': ValidationError(
                'duplicate',
                code='duplicate_phone_for_year',
            ),
        })
        self.assertTrue(_is_duplicate_course_application_phone_error(error))

    def test_does_not_mask_unrelated_validation_error(self):
        error = ValidationError({'birth_date': 'invalid'})
        self.assertFalse(_is_duplicate_course_application_phone_error(error))

    def test_recognizes_sqlite_unique_error_shape(self):
        error = IntegrityError(
            'UNIQUE constraint failed: '
            'journal_courseapplication.academic_year_id, '
            'journal_courseapplication.student_phone'
        )
        self.assertTrue(_is_duplicate_course_application_phone_error(error))

    def test_does_not_mask_unrelated_integrity_error(self):
        error = IntegrityError('NOT NULL constraint failed: other.field')
        self.assertFalse(_is_duplicate_course_application_phone_error(error))


@skipUnless(connection.vendor == 'postgresql', 'PostgreSQL concurrency test')
class CourseApplicationConcurrencyTests(JournalTestDataMixin, TransactionTestCase):
    reset_sequences = True

    def test_concurrent_same_phone_creates_exactly_one_application(self):
        year = self.create_academic_year()
        barrier = Barrier(2)
        result_lock = Lock()
        results = []
        original_full_clean = CourseApplication.full_clean

        def synchronized_full_clean(instance, *args, **kwargs):
            original_full_clean(instance, *args, **kwargs)
            barrier.wait(timeout=10)

        def create_application(last_name):
            close_old_connections()
            try:
                payload = self.application_payload(
                    last_name=last_name,
                    academic_year_id=year.pk,
                )
                with transaction.atomic():
                    CourseApplication.objects.create(**payload)
            except IntegrityError:
                outcome = 'duplicate'
            else:
                outcome = 'created'
            finally:
                close_old_connections()
            with result_lock:
                results.append(outcome)

        with patch.object(CourseApplication, 'full_clean', synchronized_full_clean):
            threads = [
                Thread(target=create_application, args=('Иванов',), daemon=True),
                Thread(target=create_application, args=('Петров',), daemon=True),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=15)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertCountEqual(results, ['created', 'duplicate'])
        self.assertEqual(
            CourseApplication.objects.filter(
                academic_year=year,
                student_phone='+7 (999) 123-45-67',
            ).count(),
            1,
        )

    def test_concurrent_same_identity_reuses_one_student_and_account(self):
        year = self.create_academic_year()
        barrier = Barrier(2)
        result_lock = Lock()
        results = []
        original_full_clean = CourseApplication.full_clean

        def synchronized_full_clean(instance, *args, **kwargs):
            original_full_clean(instance, *args, **kwargs)
            barrier.wait(timeout=10)

        def create_application(phone):
            close_old_connections()
            try:
                application = CourseApplication.objects.create(
                    **self.application_payload(
                        student_phone=phone,
                        academic_year_id=year.pk,
                    ),
                )
                outcome = (
                    application.pk,
                    application.student_id,
                    application.user_id,
                )
            except Exception as exc:  # Stored for an explicit assertion in the main thread.
                outcome = exc
            finally:
                close_old_connections()
            with result_lock:
                results.append(outcome)

        with patch.object(CourseApplication, 'full_clean', synchronized_full_clean):
            threads = [
                Thread(
                    target=create_application,
                    args=(phone,),
                    daemon=True,
                )
                for phone in ('+7 (999) 123-45-67', '+7 (999) 765-43-21')
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(timeout=20)

        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertEqual(len(results), 2)
        self.assertFalse([result for result in results if isinstance(result, Exception)], results)
        student_ids = {result[1] for result in results}
        user_ids = {result[2] for result in results}
        self.assertEqual(len(student_ids), 1)
        self.assertEqual(len(user_ids), 1)
        self.assertNotIn(None, student_ids)
        self.assertNotIn(None, user_ids)
        self.assertEqual(CourseApplication.objects.filter(academic_year=year).count(), 2)
        self.assertEqual(Student.objects.filter(pk__in=student_ids).count(), 1)
        self.assertEqual(TemporaryCredential.objects.filter(user_id__in=user_ids).count(), 1)


class AcademicYearJournalAccessTests(JournalTestDataMixin, TestCase):
    def test_student_can_select_only_years_with_enrollment(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_group = self.create_group(academic_year=old_year)
        student = self.create_student(group=old_group)
        new_year = self.create_academic_year(name='2026/2027')
        self.client.force_login(student.user)

        response = self.client.get(reverse('journal'), {'academic_year': new_year.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_academic_year'], old_year)
        self.assertEqual(list(response.context['academic_years']), [old_year])

    def test_later_application_adds_existing_student_year_without_duplicate_account(self):
        first_year = self.create_academic_year(name='2025/2026')
        first_application = CourseApplication.objects.create(**self.application_payload())
        second_year = self.create_academic_year(name='2026/2027')
        second_application = CourseApplication.objects.create(
            **self.application_payload(student_phone='+7 (999) 765-43-21'),
        )
        self.client.force_login(first_application.user)

        response = self.client.get(reverse('journal'), {'academic_year': first_year.pk})
        available_ids = set(response.context['academic_years'].values_list('pk', flat=True))

        self.assertEqual(first_application.student_id, second_application.student_id)
        self.assertEqual(first_application.user_id, second_application.user_id)
        self.assertEqual(available_ids, {first_year.pk, second_year.pk})
        self.assertEqual(response.context['selected_academic_year'], first_year)

    def test_teacher_sees_only_membership_years(self):
        old_year = self.create_academic_year(name='2025/2026')
        teacher = self.create_teacher()
        new_year = self.create_academic_year(name='2026/2027')
        self.client.force_login(teacher.user)

        response = self.client.get(reverse('journal'), {'academic_year': new_year.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_academic_year'], old_year)
        self.assertEqual(list(response.context['academic_years']), [old_year])


    def test_inactive_teacher_membership_remains_viewable_but_not_editable(self):
        year = self.create_academic_year()
        teacher = self.create_teacher()
        membership = TeacherEnrollment.objects.get(teacher=teacher, academic_year=year)
        membership.is_active = False
        membership.save()
        teacher.refresh_from_db()
        self.client.force_login(teacher.user)

        response = self.client.get(reverse('journal'), {'academic_year': year.pk})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_academic_year'], year)
        self.assertFalse(response.context['can_edit_journal'])


    def test_inactive_teacher_cannot_bypass_read_only_mode_with_post(self):
        data = self.create_base_journal()
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            academic_year=data['year'],
            date=date(2025, 10, 15),
            value='5',
        )
        membership = TeacherEnrollment.objects.get(
            teacher=data['teacher'],
            academic_year=data['year'],
        )
        membership.is_active = False
        membership.save(update_fields=['is_active'])
        self.client.force_login(data['teacher'].user)

        response = self.client.post(
            (
                f'{reverse("journal")}?group={data["group"].pk}'
                f'&subject={data["solfeggio"].pk}'
                f'&academic_year={data["year"].pk}'
            ),
            data={
                'action': 'inline_edit',
                (
                    f'grade__{data["solfeggio"].pk}__'
                    f'{data["student"].pk}__2025-10-15'
                ): '2',
            },
        )

        self.assertEqual(response.status_code, 302)
        grade.refresh_from_db()
        self.assertEqual(grade.value, '5')


class AcademicYearAdminContextTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.superuser = User.objects.create_superuser(
            username='year_admin',
            email='admin@example.com',
            password='AdminPass123!',
        )
        self.factory = RequestFactory()

    def admin_request(self, academic_year):
        request = self.factory.get('/admin/', {'academic_year': academic_year.pk})
        request.user = self.superuser
        request.session = {}
        return request

    def test_student_queryset_contains_only_people_from_selected_year(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_group = self.create_group(name='Старая группа', academic_year=old_year)
        instrument = self.create_instrument()
        old_student = self.create_student(
            full_name='Старый Ученик',
            group=old_group,
            instrument=instrument,
            username='old_student',
        )
        new_year = self.create_academic_year(name='2026/2027')
        new_group = self.create_group(name='Новая группа', academic_year=new_year)
        new_student = self.create_student(
            full_name='Новый Ученик',
            group=new_group,
            instrument=instrument,
            username='new_student',
        )

        model_admin = StudentAdmin(Student, django_admin.site)
        queryset = model_admin.get_queryset(self.admin_request(old_year))

        self.assertIn(old_student, queryset)
        self.assertNotIn(new_student, queryset)

    def test_archived_year_blocks_mutating_academic_data_but_allows_profile_edit(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_group = self.create_group(academic_year=old_year)
        instrument = self.create_instrument()
        student = self.create_student(group=old_group, instrument=instrument)
        self.create_academic_year(name='2026/2027')
        request = self.admin_request(old_year)

        group_admin = StudyGroupAdmin(StudyGroup, django_admin.site)
        student_admin = StudentAdmin(Student, django_admin.site)
        temporary_admin = TemporaryCredentialAdmin(TemporaryCredential, django_admin.site)
        student_inline = StudentInline(StudyGroup, django_admin.site)

        self.assertFalse(group_admin.has_add_permission(request))
        self.assertFalse(group_admin.has_change_permission(request, old_group))
        self.assertFalse(group_admin.has_delete_permission(request, old_group))
        self.assertTrue(student_admin.has_change_permission(request, student))
        self.assertFalse(student_admin.has_add_permission(request))
        self.assertFalse(student_admin.has_delete_permission(request, student))
        self.assertFalse(temporary_admin.has_add_permission(request))
        self.assertFalse(temporary_admin.has_change_permission(request))
        self.assertFalse(student_inline.has_add_permission(request, old_group))
        self.assertFalse(student_inline.has_change_permission(request, old_group))
        self.assertFalse(student_inline.has_delete_permission(request, old_group))

    def test_archived_student_form_keeps_profile_tabs_but_replaces_year_fields(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_group = self.create_group(academic_year=old_year)
        student = self.create_student(group=old_group)
        self.create_academic_year(name='2026/2027')
        model_admin = StudentAdmin(Student, django_admin.site)

        fieldsets = model_admin.get_fieldsets(self.admin_request(old_year), student)
        flattened_fields = {
            field
            for _title, options in fieldsets
            for field in options['fields']
        }

        self.assertIn('full_name', flattened_fields)
        self.assertIn('city_church', flattened_fields)
        self.assertIn('user', flattened_fields)
        self.assertIn('selected_year_group_display', flattened_fields)
        self.assertIn('selected_year_active_display', flattened_fields)
        self.assertNotIn('group', flattened_fields)
        self.assertNotIn('is_active', flattened_fields)

    def test_archived_course_application_change_page_does_not_raise_key_error(self):
        old_year = self.create_academic_year(name='2025/2026')
        application = CourseApplication.objects.create(**self.application_payload())
        self.create_academic_year(name='2026/2027')
        self.client.force_login(self.superuser)

        response = self.client.get(
            reverse('admin:journal_courseapplication_change', args=[application.pk]),
            {'academic_year': old_year.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, application.full_name)

    def test_group_student_inline_exposes_related_student_controls_and_card_link(self):
        year = self.create_academic_year()
        group = self.create_group(academic_year=year)
        student = self.create_student(group=group)
        enrollment = StudentEnrollment.objects.get(student=student, academic_year=year)
        request = self.admin_request(year)
        inline = StudentInline(StudyGroup, django_admin.site)
        formset_class = inline.get_formset(request, group)
        form = formset_class(instance=group).forms[0]

        self.assertIn('student_card_link', inline.fields)
        self.assertTrue(form.fields['student'].widget.can_add_related)
        self.assertTrue(form.fields['student'].widget.can_change_related)
        self.assertTrue(form.fields['student'].widget.can_delete_related)
        self.assertIn(student.full_name, str(inline.student_card_link(enrollment)))

    def test_active_group_can_enroll_student_from_previous_year(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_group = self.create_group(academic_year=old_year)
        student = self.create_student(group=old_group)
        new_year = self.create_academic_year(name='2026/2027')
        new_group = self.create_group(name='Новая группа', academic_year=new_year)
        student.refresh_from_db()
        self.assertIsNone(student.group_id)
        self.assertFalse(student.is_active)

        request = self.admin_request(new_year)
        inline = StudentInline(StudyGroup, django_admin.site)
        formset = inline.get_formset(request, new_group)(instance=new_group)
        enrollment = formset._move_student_enrollment(student, commit=True)

        student.refresh_from_db()
        self.assertEqual(enrollment.academic_year, new_year)
        self.assertEqual(enrollment.group, new_group)
        self.assertTrue(enrollment.is_active)
        self.assertEqual(student.group, new_group)
        self.assertTrue(student.is_active)

    def test_old_teacher_can_be_reused_in_active_year_assignment(self):
        old_year = self.create_academic_year(name='2025/2026')
        teacher = self.create_teacher()
        new_year = self.create_academic_year(name='2026/2027')
        new_group = self.create_group(academic_year=new_year)
        subject = self.create_subject()
        teacher.refresh_from_db()
        self.assertFalse(teacher.is_active)
        self.assertIn(teacher, assignment_teacher_queryset())

        GroupSubject.objects.create(group=new_group, subject=subject, teacher=teacher)

        teacher.refresh_from_db()
        self.assertTrue(teacher.is_active)
        self.assertTrue(
            TeacherEnrollment.objects.filter(
                teacher=teacher,
                academic_year=new_year,
                is_active=True,
            ).exists(),
        )

    def test_every_non_global_admin_disables_add_and_delete_in_archive_mode(self):
        old_year = self.create_academic_year(name='2025/2026')
        self.create_academic_year(name='2026/2027')
        request = self.admin_request(old_year)
        global_models = {CourseRegistrationSettings, PasswordRecoveryContact}

        for model, model_admin in django_admin.site._registry.items():
            if model in global_models:
                continue
            if model._meta.app_label not in {'journal', 'auth'}:
                continue
            with self.subTest(model=model._meta.label):
                self.assertFalse(model_admin.has_add_permission(request))
                self.assertFalse(model_admin.has_delete_permission(request))

    def test_global_registration_and_recovery_settings_remain_editable_in_archive_mode(self):
        old_year = self.create_academic_year(name='2025/2026')
        self.create_academic_year(name='2026/2027')
        request = self.admin_request(old_year)
        registration_admin = CourseRegistrationSettingsAdmin(
            CourseRegistrationSettings,
            django_admin.site,
        )
        recovery_admin = PasswordRecoveryContactAdmin(
            PasswordRecoveryContact,
            django_admin.site,
        )

        self.assertTrue(registration_admin.has_change_permission(request))
        self.assertTrue(recovery_admin.has_add_permission(request))

    def test_inactive_academic_year_is_read_only_even_when_active_year_is_selected(self):
        old_year = self.create_academic_year(name='2025/2026')
        active_year = self.create_academic_year(name='2026/2027')
        request = self.admin_request(active_year)
        year_admin = AcademicYearAdmin(AcademicYear, django_admin.site)

        self.assertFalse(year_admin.has_change_permission(request, old_year))
        self.assertFalse(year_admin.has_delete_permission(request, old_year))

    def test_city_church_fields_are_wide_in_admin_and_registration_forms(self):
        year = self.create_academic_year()
        group = self.create_group(academic_year=year)
        student_form = StudentAdminForm()
        application_form = CourseApplicationPublicForm()
        inline = StudentInline(StudyGroup, django_admin.site)
        inline_form = inline.get_formset(self.admin_request(year), group)(instance=group).empty_form

        self.assertIn('city-church-field', student_form.fields['city_church'].widget.attrs.get('class', ''))
        self.assertEqual(application_form.fields['city_church'].widget.attrs.get('size'), '80')
        self.assertEqual(inline_form.fields['city_church'].widget.attrs.get('size'), '80')

        css = Path('journal/static/journal/admin_dashboard.css').read_text(encoding='utf-8')
        javascript = Path('journal/static/journal/group_student_inline.js').read_text(encoding='utf-8')
        self.assertIn('min-width: min(680px, 74vw)', css)
        self.assertIn('max-width: min(1440px, 96vw)', css)
        self.assertIn("window.django.jQuery(document).on('shown.bs.modal'", javascript)

    def test_archived_admin_lists_use_assignment_snapshots(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_group = self.create_group(academic_year=old_year)
        teacher = self.create_teacher(full_name='Старое имя преподавателя')
        student = self.create_student(group=old_group)
        group_subject = GroupSubject.objects.create(
            group=old_group,
            subject=self.create_subject(name='Старое название предмета'),
            teacher=teacher,
        )
        specialty_subject = self.create_subject(
            name='Старая специальность',
            is_specialty=True,
        )
        specialty = StudentSubject.objects.create(
            student=student,
            subject=specialty_subject,
            teacher=teacher,
            academic_year=old_year,
            is_specialty=True,
        )
        self.create_academic_year(name='2026/2027')

        group_subject.subject.name = 'Новое название предмета'
        group_subject.subject.save()
        specialty_subject.name = 'Новая специальность'
        specialty_subject.save()
        teacher.full_name = 'Новое имя преподавателя'
        teacher.save()

        group_admin = StudyGroupAdmin(StudyGroup, django_admin.site)
        teacher_admin = TeacherAdmin(Teacher, django_admin.site)
        student_admin = StudentAdmin(Student, django_admin.site)
        group = group_admin.get_queryset(self.admin_request(old_year)).get(pk=old_group.pk)
        selected_teacher = teacher_admin.get_queryset(self.admin_request(old_year)).get(pk=teacher.pk)
        selected_student = student_admin.get_queryset(self.admin_request(old_year)).get(pk=student.pk)

        self.assertIn('Старое название предмета', str(group_admin.subjects_display_short(group)))
        self.assertIn('Старое имя преподавателя', str(group_admin.teachers_display_short(group)))
        self.assertIn('Старое название предмета', str(teacher_admin.group_subjects_short(selected_teacher)))
        self.assertEqual(
            str(student_admin.specialty_subject_display(selected_student)),
            specialty.subject_name_snapshot,
        )
        self.assertEqual(
            str(student_admin.specialty_teacher_display(selected_student)),
            specialty.teacher_name_snapshot,
        )

    def test_temporary_credentials_are_scoped_to_selected_academic_year(self):
        old_year = self.create_academic_year(name='2025/2026')
        old_application = CourseApplication.objects.create(**self.application_payload())
        active_year = self.create_academic_year(name='2026/2027')
        active_application = CourseApplication.objects.create(
            **self.application_payload(
                last_name='Петров',
                student_phone='+7 (999) 765-43-21',
            ),
        )
        staff_user = User.objects.create_user(
            username='yearless_staff',
            password='Pass12345!',
            is_staff=True,
        )
        staff_credential = TemporaryCredential.objects.create(
            user=staff_user,
            login=staff_user.username,
            temporary_password='StaffTemp123!',
        )

        old_credentials = filter_temporary_credentials_for_year(
            TemporaryCredential.objects.all(),
            old_year,
        )
        active_credentials = filter_temporary_credentials_for_year(
            TemporaryCredential.objects.all(),
            active_year,
        )

        self.assertIn(old_application.temporary_credential, old_credentials)
        self.assertNotIn(active_application.temporary_credential, old_credentials)
        self.assertNotIn(staff_credential, old_credentials)
        self.assertIn(active_application.temporary_credential, active_credentials)
        self.assertIn(staff_credential, active_credentials)


class AdminDashboardTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='dashboard_admin',
            password='Pass12345!',
            email='dashboard-admin@example.com',
        )

    def test_archived_academic_year_records_are_read_only_in_admin(self):
        data = self.create_base_journal()
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )
        data['group'].refresh_from_db()

        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[StudyGroup]

        self.assertFalse(model_admin.has_change_permission(request, data['group']))
        self.assertFalse(model_admin.has_delete_permission(request, data['group']))

    def test_archived_group_page_uses_enrollment_and_assignment_snapshots(self):
        data = self.create_base_journal()
        archived_student_name = data['student'].full_name
        archived_subject_name = data['solfeggio'].name
        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )
        data['student'].refresh_from_db()
        data['student'].full_name = 'Текущее имя ученика'
        data['student'].save()
        data['solfeggio'].name = 'Текущее название предмета'
        data['solfeggio'].save()
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.get(
            reverse('admin:journal_studygroup_change', args=[data['group'].pk]),
        )

        self.assertContains(response, archived_student_name)
        self.assertContains(response, archived_subject_name)
        self.assertNotContains(response, 'name="student_enrollments-0-student"')
        self.assertNotContains(response, 'name="group_subjects-0-subject"')

    def test_admin_dashboard_links_recovery_settings_and_related_data(self):
        self.create_base_journal()
        PasswordRecoveryContact.objects.create(
            name='Администратор',
            phone='+7 (999) 123-45-67',
            messengers='Telegram',
        )
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.get(reverse('admin:index'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Настройки восстановления')
        self.assertContains(response, reverse('admin:journal_passwordrecoverycontact_changelist'))
        self.assertContains(response, 'Связанные данные')
        self.assertContains(response, 'Групповые предметы')
        self.assertContains(response, reverse('admin:journal_groupsubject_changelist'))
        self.assertContains(response, 'Индивидуальные предметы')
        self.assertContains(response, reverse('admin:journal_studentsubject_changelist'))
        self.assertContains(response, 'Квалификации преподавателей')
        self.assertContains(response, reverse('admin:journal_teachersubject_changelist'))
        self.assertContains(response, 'Инструкция')
        self.assertContains(response, reverse('admin_guide'))

    def test_admin_guide_is_visible_only_for_superuser(self):
        staff_user = User.objects.create_user(
            username='guide_staff',
            password='Pass12345!',
            is_staff=True,
        )

        self.client.login(username='guide_staff', password='Pass12345!')
        staff_response = self.client.get(reverse('admin_guide'))
        self.assertEqual(staff_response.status_code, 302)

        self.client.login(username='dashboard_admin', password='Pass12345!')
        admin_response = self.client.get(reverse('admin_guide'))

        self.assertEqual(admin_response.status_code, 200)
        self.assertContains(admin_response, 'Как работать с журналом')
        self.assertContains(admin_response, reverse('admin:journal_academicyear_changelist'))
        self.assertContains(admin_response, 'Архивный год можно открыть в фильтре журнала')
        self.assertContains(admin_response, reverse('admin:journal_student_changelist'))
        self.assertContains(admin_response, reverse('admin_data_tools'))

    def test_admin_changelist_add_button_is_ordered_before_search(self):
        css = Path('journal/static/journal/admin_dashboard.css').read_text(encoding='utf-8')

        self.assertIn('body.change-list #change-list-filters .object-tools', css)
        self.assertIn('order: 1;', css)
        self.assertIn('body.change-list #changelist-search', css)
        self.assertIn('order: 2;', css)

    def test_admin_changelists_show_table_descriptions(self):
        models = (
            User,
            Group,
            AcademicYear,
            Instrument,
            Subject,
            StudyGroup,
            Teacher,
            Student,
            TeacherSubject,
            GroupSubject,
            StudentSubject,
            Grade,
            SubjectResult,
            CourseApplication,
            TemporaryCredential,
            CourseRegistrationSettings,
            PasswordRecoveryContact,
        )

        for model in models:
            with self.subTest(model=model.__name__):
                model_admin = django_admin.site._registry[model]
                self.assertTrue(model_admin.changelist_description)
                self.assertEqual(
                    model_admin.change_list_template,
                    'admin/journal/change_list_with_description.html',
                )

        self.client.login(username='dashboard_admin', password='Pass12345!')
        response = self.client.get(reverse('admin:journal_studygroup_changelist'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, django_admin.site._registry[StudyGroup].changelist_description)
        self.assertContains(response, 'journal-changelist-description')

    def test_related_models_are_visible_in_admin_without_teacher_qualification_inlines(self):
        request = type('Request', (), {'user': self.admin_user})()

        for model in (GroupSubject, StudentSubject, TeacherSubject):
            with self.subTest(model=model.__name__):
                model_admin = django_admin.site._registry[model]
                self.assertTrue(model_admin.get_model_perms(request).get('view'))

        self.assertEqual(
            [inline.model for inline in django_admin.site._registry[Subject].inlines],
            [GroupSubject, StudentSubject],
        )
        self.assertEqual(
            [inline.model for inline in django_admin.site._registry[Teacher].inlines],
            [GroupSubject, StudentSubject],
        )

    def test_subject_admin_shows_assignment_inline_for_subject_type(self):
        group_subject = self.create_subject(name='Групповой предмет')
        individual_subject = self.create_subject(name='Индивидуальный предмет', is_specialty=True)
        self.client.login(username='dashboard_admin', password='Pass12345!')

        group_response = self.client.get(reverse('admin:journal_subject_change', args=[group_subject.pk]))
        self.assertContains(group_response, 'Индивидуальный предмет')
        self.assertContains(group_response, 'Группы, где есть этот предмет')
        self.assertNotContains(group_response, 'Индивидуальные ученики по этому предмету')

        individual_response = self.client.get(
            reverse('admin:journal_subject_change', args=[individual_subject.pk])
        )
        self.assertContains(individual_response, 'Индивидуальный предмет')
        self.assertContains(individual_response, 'Индивидуальные ученики по этому предмету')
        self.assertNotContains(individual_response, 'Группы, где есть этот предмет')

    def test_subject_admin_autocomplete_filters_subjects_by_assignment_type(self):
        group_subject = self.create_subject(name='Групповой предмет')
        individual_subject = self.create_subject(name='Индивидуальный предмет', is_specialty=True)
        model_admin = django_admin.site._registry[Subject]

        group_request = type(
            'Request',
            (),
            {
                'user': self.admin_user,
                'GET': {'field_name': 'subject', 'model_name': 'groupsubject'},
            },
        )()
        group_queryset, _ = model_admin.get_search_results(
            group_request,
            Subject.objects.all(),
            '',
        )
        self.assertEqual(set(group_queryset), {group_subject})

        individual_request = type(
            'Request',
            (),
            {
                'user': self.admin_user,
                'GET': {'field_name': 'subject', 'model_name': 'studentsubject'},
            },
        )()
        individual_queryset, _ = model_admin.get_search_results(
            individual_request,
            Subject.objects.all(),
            '',
        )
        self.assertEqual(set(individual_queryset), {individual_subject})

    def test_group_admin_allows_adding_students_inline(self):
        year = self.create_academic_year()
        group = StudyGroup.objects.create(name='Группа с учениками', academic_year=year)
        source_group = StudyGroup.objects.create(name='Исходная группа', academic_year=year)
        instrument = self.create_instrument()
        student = Student.objects.create(
            full_name='Готовый Ученик',
            group=source_group,
            instrument=instrument,
            city_church='Тамбов / Центр',
            is_active=True,
        )
        self.client.login(username='dashboard_admin', password='Pass12345!')

        get_response = self.client.get(reverse('admin:journal_studygroup_change', args=[group.pk]))
        self.assertContains(get_response, 'name="student_enrollments-0-student"')
        self.assertContains(get_response, 'name="student_enrollments-0-city_church"')
        self.assertContains(get_response, f'value="{student.pk}"')
        self.assertContains(get_response, 'data-city-church="Тамбов / Центр"')
        self.assertContains(get_response, 'data-student-city-target="1"')
        self.assertContains(get_response, 'disabled')
        self.assertNotContains(get_response, 'name="student_enrollments-0-full_name"')
        self.assertNotContains(get_response, 'name="student_enrollments-0-instrument"')

        response = self.client.post(
            reverse('admin:journal_studygroup_change', args=[group.pk]),
            data={
                'name': group.name,
                'academic_year': year.pk,
                'is_active': 'on',
                'group_subjects-TOTAL_FORMS': '0',
                'group_subjects-INITIAL_FORMS': '0',
                'group_subjects-MIN_NUM_FORMS': '0',
                'group_subjects-MAX_NUM_FORMS': '1000',
                'student_enrollments-TOTAL_FORMS': '1',
                'student_enrollments-INITIAL_FORMS': '0',
                'student_enrollments-MIN_NUM_FORMS': '0',
                'student_enrollments-MAX_NUM_FORMS': '1000',
                'student_enrollments-0-id': '',
                'student_enrollments-0-group': group.pk,
                'student_enrollments-0-student': student.pk,
                'student_enrollments-0-city_church': 'Воронеж / Север',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        student.refresh_from_db()
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(student.group, group)
        self.assertEqual(student.city_church, 'Тамбов / Центр')

    def test_group_admin_shows_inline_error_for_duplicate_group_subject(self):
        year = self.create_academic_year()
        group = StudyGroup.objects.create(name='Группа с дублем предмета', academic_year=year)
        subject = self.create_subject(name='Групповой дубль')
        teacher = self.create_teacher(username='duplicate_group_teacher')
        assignment = GroupSubject.objects.create(
            group=group,
            subject=subject,
            teacher=teacher,
            sort_order=10,
        )
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_studygroup_change', args=[group.pk]),
            data={
                'name': group.name,
                'academic_year': year.pk,
                'is_active': 'on',
                'group_subjects-TOTAL_FORMS': '2',
                'group_subjects-INITIAL_FORMS': '1',
                'group_subjects-MIN_NUM_FORMS': '0',
                'group_subjects-MAX_NUM_FORMS': '1000',
                'group_subjects-0-id': assignment.pk,
                'group_subjects-0-group': group.pk,
                'group_subjects-0-subject': subject.pk,
                'group_subjects-0-teacher': teacher.pk,
                'group_subjects-0-sort_order': '10',
                'group_subjects-0-is_active': 'on',
                'group_subjects-1-id': '',
                'group_subjects-1-group': group.pk,
                'group_subjects-1-subject': subject.pk,
                'group_subjects-1-teacher': teacher.pk,
                'group_subjects-1-sort_order': '20',
                'group_subjects-1-is_active': 'on',
                'student_enrollments-TOTAL_FORMS': '0',
                'student_enrollments-INITIAL_FORMS': '0',
                'student_enrollments-MIN_NUM_FORMS': '0',
                'student_enrollments-MAX_NUM_FORMS': '1000',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'В этой группе уже есть такой предмет.',
        )

    def test_group_admin_allows_editing_and_deleting_students_inline(self):
        year = self.create_academic_year()
        group = StudyGroup.objects.create(name='Группа с редактированием', academic_year=year)
        fallback_group = StudyGroup.objects.create(
            name=CourseApplication.STUDENT_COURSE_GROUP_NAME,
            academic_year=year,
        )
        instrument = self.create_instrument()
        student_to_edit = Student.objects.create(
            full_name='Старое Имя',
            group=group,
            instrument=instrument,
            city_church='Старый город / церковь',
            is_active=True,
        )
        student_to_delete = Student.objects.create(
            full_name='Удаляемый Ученик',
            group=group,
            instrument=instrument,
            is_active=True,
        )
        enrollment_to_edit = student_to_edit.enrollment_for_year(year)
        enrollment_to_delete = student_to_delete.enrollment_for_year(year)
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_studygroup_change', args=[group.pk]),
            data={
                'name': group.name,
                'academic_year': year.pk,
                'is_active': 'on',
                'group_subjects-TOTAL_FORMS': '0',
                'group_subjects-INITIAL_FORMS': '0',
                'group_subjects-MIN_NUM_FORMS': '0',
                'group_subjects-MAX_NUM_FORMS': '1000',
                'student_enrollments-TOTAL_FORMS': '2',
                'student_enrollments-INITIAL_FORMS': '2',
                'student_enrollments-MIN_NUM_FORMS': '0',
                'student_enrollments-MAX_NUM_FORMS': '1000',
                'student_enrollments-0-id': enrollment_to_edit.pk,
                'student_enrollments-0-group': group.pk,
                'student_enrollments-0-student': student_to_edit.pk,
                'student_enrollments-0-city_church': 'Новый город / церковь',
                'student_enrollments-1-id': enrollment_to_delete.pk,
                'student_enrollments-1-group': group.pk,
                'student_enrollments-1-student': student_to_delete.pk,
                'student_enrollments-1-city_church': student_to_delete.city_church,
                'student_enrollments-1-DELETE': 'on',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        student_to_edit.refresh_from_db()
        student_to_delete.refresh_from_db()
        self.assertEqual(student_to_edit.city_church, 'Старый город / церковь')
        self.assertEqual(student_to_delete.group, fallback_group)

    def test_admin_assignment_forms_limit_field_choices(self):
        data = self.create_base_journal()
        inactive_group = self.create_group(
            name='Неактивная группа',
            academic_year=data['year'],
        )
        inactive_group.is_active = False
        inactive_group.save()
        inactive_teacher = self.create_teacher(username='inactive_teacher')
        inactive_teacher.is_active = False
        inactive_teacher.save()
        inactive_subject = self.create_subject(name='Неактивный предмет')
        inactive_subject.is_active = False
        inactive_subject.save()

        group_form = GroupSubjectAdminForm()
        student_form = StudentSubjectAdminForm()
        group_admin = django_admin.site._registry[GroupSubject]
        student_admin = django_admin.site._registry[StudentSubject]

        self.assertIs(group_admin.form, GroupSubjectAdminForm)
        self.assertEqual(group_admin.autocomplete_fields, ())
        self.assertIn('journal/admin_assignment_dependencies.js', GroupSubjectAdminForm.Media.js)
        self.assertNotIn(inactive_group, group_form.fields['group'].queryset)
        self.assertNotIn(inactive_teacher, group_form.fields['teacher'].queryset)
        self.assertNotIn(inactive_subject, group_form.fields['subject'].queryset)
        self.assertIn(data['solfeggio'], group_form.fields['subject'].queryset)
        self.assertNotIn(data['specialty'], group_form.fields['subject'].queryset)
        self.assertIs(student_admin.form, StudentSubjectAdminForm)
        self.assertEqual(student_admin.autocomplete_fields, ())
        self.assertIn('journal/admin_assignment_dependencies.js', StudentSubjectAdminForm.Media.js)
        self.assertIn(data['specialty'], student_form.fields['subject'].queryset)
        self.assertNotIn(data['solfeggio'], student_form.fields['subject'].queryset)

    def test_student_subject_admin_form_autofills_specialty_flag_from_subject(self):
        data = self.create_base_journal()
        extra_subject = self.create_subject(
            name='Индивидуальная импровизация',
            is_specialty=True,
        )
        student = self.create_student(
            full_name='Ученик без специальности',
            group=data['group'],
            instrument=data['instrument'],
            username='student_without_specialty',
        )

        extra_form = StudentSubjectAdminForm(
            data={
                'student': student.pk,
                'subject': extra_subject.pk,
                'teacher': data['teacher'].pk,
                'is_specialty': 'on',
                'is_active': 'on',
            },
        )
        specialty_form = StudentSubjectAdminForm(
            data={
                'student': student.pk,
                'subject': data['specialty'].pk,
                'teacher': data['other_teacher'].pk,
                'is_active': 'on',
            },
        )

        self.assertTrue(extra_form.is_valid(), extra_form.errors)
        self.assertFalse(extra_form.cleaned_data['is_specialty'])
        self.assertTrue(specialty_form.is_valid(), specialty_form.errors)
        self.assertTrue(specialty_form.cleaned_data['is_specialty'])

    def test_subject_result_admin_form_limits_subjects_by_student_assignments(self):
        data = self.create_base_journal()
        unassigned_subject = self.create_subject(name='Неназначенный предмет')

        form = SubjectResultAdminForm(
            data={
                'student': data['student'].pk,
                'academic_year': data['year'].pk,
                'subject': '',
                'exam_grade': '',
                'final_grade': '',
            },
        )

        subject_queryset = form.fields['subject'].queryset
        self.assertIn(data['solfeggio'], subject_queryset)
        self.assertIn(data['specialty'], subject_queryset)
        self.assertNotIn(unassigned_subject, subject_queryset)

    def test_grade_admin_change_form_has_single_group_field(self):
        model_admin = django_admin.site._registry[Grade]
        fieldsets = model_admin.get_fieldsets(type('Request', (), {'user': self.admin_user})())
        fields = [
            field
            for _name, options in fieldsets
            for field in options.get('fields', ())
        ]

        self.assertIn('group', fields)
        self.assertNotIn('student_group_display', fields)

    def test_group_admin_detaches_student_when_no_fallback_group_exists(self):
        year = self.create_academic_year()
        group = StudyGroup.objects.create(name='Ученики курсов', academic_year=year)
        instrument = self.create_instrument()
        student = Student.objects.create(
            full_name='Открепляемый Ученик',
            group=group,
            instrument=instrument,
            is_active=True,
        )
        enrollment = student.enrollment_for_year(year)
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_studygroup_change', args=[group.pk]),
            data={
                'name': group.name,
                'academic_year': year.pk,
                'is_active': 'on',
                'group_subjects-TOTAL_FORMS': '0',
                'group_subjects-INITIAL_FORMS': '0',
                'group_subjects-MIN_NUM_FORMS': '0',
                'group_subjects-MAX_NUM_FORMS': '1000',
                'student_enrollments-TOTAL_FORMS': '1',
                'student_enrollments-INITIAL_FORMS': '1',
                'student_enrollments-MIN_NUM_FORMS': '0',
                'student_enrollments-MAX_NUM_FORMS': '1000',
                'student_enrollments-0-id': enrollment.pk,
                'student_enrollments-0-group': group.pk,
                'student_enrollments-0-student': student.pk,
                'student_enrollments-0-city_church': student.city_church,
                'student_enrollments-0-DELETE': 'on',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        student.refresh_from_db()
        self.assertIsNone(student.group)

    def test_student_admin_shows_inline_error_for_duplicate_individual_subject(self):
        data = self.create_base_journal()
        student = data['student']
        assignment = StudentSubject.objects.get(
            student=student,
            subject=data['specialty'],
        )
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_student_change', args=[student.pk]),
            data={
                'full_name': student.full_name,
                'gender': student.gender,
                'birth_date': student.birth_date.isoformat() if student.birth_date else '',
                'group': student.group_id,
                'instrument': student.instrument_id,
                'is_active': 'on',
                'student_phone': student.student_phone,
                'parent_contacts': student.parent_contacts,
                'city_church': student.city_church,
                'music_education': student.music_education,
                'comments': student.comments,
                'user': student.user_id,
                'individual_subjects-TOTAL_FORMS': '2',
                'individual_subjects-INITIAL_FORMS': '1',
                'individual_subjects-MIN_NUM_FORMS': '0',
                'individual_subjects-MAX_NUM_FORMS': '1000',
                'individual_subjects-0-id': assignment.pk,
                'individual_subjects-0-student': student.pk,
                'individual_subjects-0-subject': data['specialty'].pk,
                'individual_subjects-0-teacher': data['other_teacher'].pk,
                'individual_subjects-0-is_specialty': 'on',
                'individual_subjects-0-is_active': 'on',
                'individual_subjects-1-id': '',
                'individual_subjects-1-student': student.pk,
                'individual_subjects-1-subject': data['specialty'].pk,
                'individual_subjects-1-teacher': data['other_teacher'].pk,
                'individual_subjects-1-is_active': 'on',
                'subject_results-TOTAL_FORMS': '0',
                'subject_results-INITIAL_FORMS': '0',
                'subject_results-MIN_NUM_FORMS': '0',
                'subject_results-MAX_NUM_FORMS': '1000',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            'У ученика уже есть такой индивидуальный предмет.',
        )

    def test_student_admin_uses_prefetched_specialty_without_row_queries(self):
        data = self.create_base_journal()
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Student]
        student = model_admin.get_queryset(request).get(pk=data['student'].pk)

        with CaptureQueriesContext(connection) as captured_queries:
            self.assertEqual(model_admin.specialty_teacher_display(student), data['other_teacher'])
            self.assertEqual(model_admin.specialty_subject_display(student), data['specialty'])

        self.assertEqual(
            len(captured_queries),
            0,
            [query['sql'] for query in captured_queries],
        )

    def test_grade_admin_uses_assignment_annotations_without_row_queries(self):
        data = self.create_base_journal()
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            date=date(2025, 10, 8),
            value='5',
        )
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Grade]
        grade_from_queryset = model_admin.get_queryset(request).get(pk=grade.pk)

        with CaptureQueriesContext(connection) as captured_queries:
            self.assertEqual(model_admin.source_type_display(grade_from_queryset), 'Групповой предмет')

        self.assertEqual(
            len(captured_queries),
            0,
            [query['sql'] for query in captured_queries],
        )

    def test_teacher_admin_creates_user_and_temporary_credentials_on_manual_add(self):
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Teacher]
        teacher = Teacher(
            full_name='Новый Преподаватель',
            email='new-teacher@example.com',
        )

        model_admin.save_model(request, teacher, form=None, change=False)

        teacher.refresh_from_db()
        self.assertIsNotNone(teacher.user)
        self.assertEqual(teacher.user.username, 'Преподаватель Новый')
        self.assertTrue(teacher.user.groups.filter(name='Преподаватель').exists())
        credential = TemporaryCredential.objects.get(login=teacher.user.username)
        self.assertEqual(credential.user, teacher.user)
        self.assertTrue(credential.temporary_password)
        self.assertTrue(teacher.user.check_password(credential.temporary_password))

    def test_teacher_admin_update_preserves_password_and_temporary_password(self):
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Teacher]
        teacher = Teacher(full_name='Новый Преподаватель')
        model_admin.save_model(request, teacher, form=None, change=False)

        user = teacher.user
        credential = TemporaryCredential.objects.get(user=user)
        original_password_hash = user.password
        original_temporary_password = credential.temporary_password

        teacher.full_name = 'Обновлённый Преподаватель'
        teacher.phone = '+7 (999) 555-44-33'
        model_admin.save_model(request, teacher, form=None, change=True)

        user.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(user.password, original_password_hash)
        self.assertEqual(credential.temporary_password, original_temporary_password)
        self.assertTrue(user.check_password(original_temporary_password))

    def test_teacher_admin_does_not_reset_password_of_selected_existing_user(self):
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Teacher]
        existing_user = User.objects.create_user(
            username='existing teacher account',
            password='ExistingPass123!',
        )
        original_password_hash = existing_user.password
        teacher = Teacher(
            full_name='Преподаватель с аккаунтом',
            user=existing_user,
        )

        model_admin.save_model(request, teacher, form=None, change=False)
        existing_user.refresh_from_db()

        self.assertEqual(existing_user.password, original_password_hash)
        self.assertFalse(TemporaryCredential.objects.filter(user=existing_user).exists())

    def test_student_admin_creates_user_and_temporary_credentials_on_manual_add(self):
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Student]
        student = Student(
            full_name='Новый Ученик',
            group=self.create_group(),
            instrument=self.create_instrument(),
            student_phone='+7 (999) 111-22-33',
        )

        model_admin.save_model(request, student, form=None, change=False)

        student.refresh_from_db()
        self.assertIsNotNone(student.user)
        self.assertEqual(student.user.username, 'Ученик Новый')
        self.assertTrue(student.user.groups.filter(name='Ученик').exists())
        credential = TemporaryCredential.objects.get(login=student.user.username)
        self.assertEqual(credential.user, student.user)
        self.assertEqual(credential.student_phone, '+7 (999) 111-22-33')
        self.assertTrue(credential.temporary_password)
        self.assertTrue(student.user.check_password(credential.temporary_password))

    def test_student_admin_update_preserves_password_and_temporary_password(self):
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Student]
        student = Student(
            full_name='Новый Ученик',
            group=self.create_group(),
            instrument=self.create_instrument(),
            student_phone='+7 (999) 111-22-33',
        )
        model_admin.save_model(request, student, form=None, change=False)

        user = student.user
        credential = TemporaryCredential.objects.get(user=user)
        original_password_hash = user.password
        original_temporary_password = credential.temporary_password

        student.full_name = 'Обновлённый Ученик'
        student.student_phone = '+7 (999) 111-22-44'
        model_admin.save_model(request, student, form=None, change=True)

        user.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(user.password, original_password_hash)
        self.assertEqual(credential.temporary_password, original_temporary_password)
        self.assertEqual(credential.student_phone, '+7 (999) 111-22-44')
        self.assertTrue(user.check_password(original_temporary_password))

    def test_student_admin_does_not_reset_password_of_selected_existing_user(self):
        request = type('Request', (), {'user': self.admin_user})()
        model_admin = django_admin.site._registry[Student]
        existing_user = User.objects.create_user(
            username='existing student account',
            password='ExistingPass123!',
        )
        original_password_hash = existing_user.password
        student = Student(
            full_name='Ученик с аккаунтом',
            group=self.create_group(),
            instrument=self.create_instrument(),
            user=existing_user,
        )

        model_admin.save_model(request, student, form=None, change=False)
        existing_user.refresh_from_db()

        self.assertEqual(existing_user.password, original_password_hash)
        self.assertTrue(existing_user.groups.filter(name='Ученик').exists())
        self.assertFalse(TemporaryCredential.objects.filter(user=existing_user).exists())

    def test_teacher_admin_allows_adding_group_and_individual_subjects_inline(self):
        year = self.create_academic_year()
        group = StudyGroup.objects.create(name='Группа преподавателя', academic_year=year)
        instrument = self.create_instrument()
        student = Student.objects.create(
            full_name='Индивидуальный Ученик',
            group=group,
            instrument=instrument,
        )
        teacher = self.create_teacher(
            full_name='Преподаватель Назначений',
            username='teacher_assignments',
        )
        group_subject = self.create_subject(name='Групповой предмет')
        individual_subject = self.create_subject(name='Специальность назначений', is_specialty=True)
        self.client.login(username='dashboard_admin', password='Pass12345!')

        get_response = self.client.get(reverse('admin:journal_teacher_change', args=[teacher.pk]))
        self.assertContains(get_response, 'name="group_subjects-0-group"')
        self.assertContains(get_response, 'name="individual_subjects-0-student"')

        response = self.client.post(
            reverse('admin:journal_teacher_change', args=[teacher.pk]),
            data={
                'full_name': teacher.full_name,
                'birth_date': '',
                'phone': '',
                'email': '',
                'comments': '',
                'user': teacher.user_id,
                'is_active': 'on',
                'group_subjects-TOTAL_FORMS': '1',
                'group_subjects-INITIAL_FORMS': '0',
                'group_subjects-MIN_NUM_FORMS': '0',
                'group_subjects-MAX_NUM_FORMS': '1000',
                'group_subjects-0-id': '',
                'group_subjects-0-teacher': teacher.pk,
                'group_subjects-0-group': group.pk,
                'group_subjects-0-subject': group_subject.pk,
                'group_subjects-0-sort_order': '10',
                'group_subjects-0-is_active': 'on',
                'individual_subjects-TOTAL_FORMS': '1',
                'individual_subjects-INITIAL_FORMS': '0',
                'individual_subjects-MIN_NUM_FORMS': '0',
                'individual_subjects-MAX_NUM_FORMS': '1000',
                'individual_subjects-0-id': '',
                'individual_subjects-0-teacher': teacher.pk,
                'individual_subjects-0-student': student.pk,
                'individual_subjects-0-subject': individual_subject.pk,
                'individual_subjects-0-is_specialty': 'on',
                'individual_subjects-0-is_active': 'on',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            GroupSubject.objects.filter(
                group=group,
                subject=group_subject,
                teacher=teacher,
            ).exists(),
        )
        self.assertTrue(
            StudentSubject.objects.filter(
                student=student,
                subject=individual_subject,
                teacher=teacher,
                is_specialty=True,
            ).exists(),
        )

    def test_changing_group_assignment_in_admin_cascades_to_existing_grades(self):
        data = self.create_base_journal()
        assignment = GroupSubject.objects.get(
            group=data['group'],
            subject=data['solfeggio'],
        )
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            teacher=data['teacher'],
            date=date(2025, 10, 8),
            value='5',
        )
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_groupsubject_change', args=[assignment.pk]),
            data={
                'group': data['group'].pk,
                'subject': data['solfeggio'].pk,
                'teacher': data['other_teacher'].pk,
                'sort_order': assignment.sort_order,
                'is_active': 'on',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        grade.refresh_from_db()
        self.assertEqual(grade.teacher, data['other_teacher'])

    def test_changing_individual_assignment_in_admin_cascades_to_existing_grades(self):
        data = self.create_base_journal()
        assignment = StudentSubject.objects.get(
            student=data['student'],
            subject=data['specialty'],
        )
        grade = Grade.objects.create(
            student=data['student'],
            subject=data['specialty'],
            teacher=data['other_teacher'],
            date=date(2025, 10, 9),
            value='4',
        )
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_studentsubject_change', args=[assignment.pk]),
            data={
                'student': data['student'].pk,
                'subject': data['specialty'].pk,
                'teacher': data['teacher'].pk,
                'is_specialty': 'on',
                'is_active': 'on',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        grade.refresh_from_db()
        self.assertEqual(grade.teacher, data['teacher'])

    def test_student_birth_date_change_ignores_unchanged_historical_subject_results(self):
        data = self.create_base_journal()
        assignment = GroupSubject.objects.get(
            group=data['group'],
            subject=data['solfeggio'],
        )
        individual_assignment = StudentSubject.objects.get(
            student=data['student'],
            subject=data['specialty'],
        )
        result = SubjectResult.objects.create(
            student=data['student'],
            subject=data['solfeggio'],
            academic_year=data['year'],
            exam_grade='5',
            final_grade='5',
        )
        assignment.is_active = False
        assignment.save()
        self.client.login(username='dashboard_admin', password='Pass12345!')

        response = self.client.post(
            reverse('admin:journal_student_change', args=[data['student'].pk]),
            data={
                'full_name': data['student'].full_name,
                'gender': data['student'].gender,
                'birth_date': '2011-02-02',
                'group': data['group'].pk,
                'instrument': data['instrument'].pk,
                'is_active': 'on',
                'student_phone': data['student'].student_phone,
                'parent_contacts': data['student'].parent_contacts,
                'city_church': data['student'].city_church,
                'music_education': data['student'].music_education,
                'comments': data['student'].comments,
                'user': data['student'].user_id,
                'individual_subjects-TOTAL_FORMS': '1',
                'individual_subjects-INITIAL_FORMS': '1',
                'individual_subjects-MIN_NUM_FORMS': '0',
                'individual_subjects-MAX_NUM_FORMS': '1000',
                'individual_subjects-0-id': individual_assignment.pk,
                'individual_subjects-0-student': data['student'].pk,
                'individual_subjects-0-subject': data['specialty'].pk,
                'individual_subjects-0-teacher': data['other_teacher'].pk,
                'individual_subjects-0-is_specialty': 'on',
                'individual_subjects-0-is_active': 'on',
                'subject_results-TOTAL_FORMS': '1',
                'subject_results-INITIAL_FORMS': '1',
                'subject_results-MIN_NUM_FORMS': '0',
                'subject_results-MAX_NUM_FORMS': '1000',
                'subject_results-0-id': result.pk,
                'subject_results-0-student': data['student'].pk,
                'subject_results-0-academic_year': data['year'].pk,
                'subject_results-0-subject': data['solfeggio'].pk,
                'subject_results-0-exam_grade': '5',
                'subject_results-0-final_grade': '5',
                '_save': 'Save',
            },
        )

        self.assertEqual(response.status_code, 302)
        data['student'].refresh_from_db()
        self.assertEqual(data['student'].birth_date, date(2011, 2, 2))


class PasswordRecoveryViewTests(TestCase):
    def test_login_page_contains_password_help_link(self):
        response = self.client.get(reverse('login'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Забыли пароль?')
        self.assertContains(response, reverse('password_help'))

    def test_password_help_lists_only_active_contacts_in_configured_order(self):
        second = PasswordRecoveryContact.objects.create(
            name='Второй администратор',
            phone='8 999 222 33 44',
            messengers='WhatsApp',
            display_order=20,
        )
        first = PasswordRecoveryContact.objects.create(
            name='Первый администратор',
            phone='+7 (999) 111-22-33',
            messengers='Telegram, MAX',
            display_order=10,
        )
        PasswordRecoveryContact.objects.create(
            name='Скрытый администратор',
            phone='+7 (999) 000-00-00',
            messengers='Telegram',
            is_active=False,
        )

        response = self.client.get(reverse('password_help'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(list(response.context['contacts']), [first, second])
        self.assertContains(response, 'Первый администратор')
        self.assertContains(response, '+7 (999) 111-22-33')
        self.assertContains(response, 'Telegram, MAX')
        self.assertContains(response, 'Второй администратор')
        self.assertNotContains(response, 'Скрытый администратор')

    def test_password_help_has_empty_state_without_configured_contacts(self):
        response = self.client.get(reverse('password_help'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Контакты администраторов пока не опубликованы')

    def test_recovery_contact_normalizes_values_and_builds_phone_link(self):
        contact = PasswordRecoveryContact.objects.create(
            name='  Администратор  ',
            phone='8 999 123 45 67',
            messengers='  Telegram, WhatsApp  ',
        )

        self.assertEqual(contact.name, 'Администратор')
        self.assertEqual(contact.phone, '+7 (999) 123-45-67')
        self.assertEqual(contact.messengers, 'Telegram, WhatsApp')
        self.assertEqual(contact.phone_uri, 'tel:+79991234567')


class CourseRegistrationViewTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.create_academic_year(name='2025/2026')
        CourseRegistrationSettings.objects.create(
            pk=1,
            telegram_group_url='https://t.me/test_group',
            minimum_registration_age=14,
        )

    def test_registration_page_creates_confirmed_application_and_shows_credentials(
        self,
    ):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            response = self.client.post(
                reverse('course_registration'),
                data=self.application_form_payload(),
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseApplication.objects.count(), 1)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(TemporaryCredential.objects.count(), 1)
        self.assertContains(response, 'Иванов Иван')
        self.assertContains(response, 'Temp12345!')

    def test_registration_page_rejects_duplicate_phone_without_second_application(
        self,
    ):
        CourseApplication.objects.create(**self.application_payload())

        response = self.client.post(
            reverse('course_registration'),
            data=self.application_form_payload(
                last_name='Петров',
                first_name='Пётр',
                student_phone='8 999 123 45 67',
            ),
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(CourseApplication.objects.count(), 1)
        self.assertIn(
            'Ученик с таким номером телефона уже зарегистрирован.',
            response.content.decode('utf-8'),
        )

    def test_duplicate_phone_is_checked_inside_academic_year_only(self):
        first_application = CourseApplication.objects.create(**self.application_payload())

        with self.assertRaisesMessage(ValidationError, 'Ученик с таким номером телефона уже зарегистрирован.'):
            CourseApplication.objects.create(
                **self.application_payload(
                    last_name='Петров',
                    first_name='Пётр',
                    student_phone='8 999 123 45 67',
                ),
            )

        AcademicYear.objects.create(
            name='2026/2027',
            starts_on=date(2026, 9, 1),
            ends_on=date(2027, 8, 31),
        )
        second_application = CourseApplication.objects.create(
            **self.application_payload(
                last_name='Петров',
                first_name='Пётр',
                student_phone='8 999 123 45 67',
            ),
        )

        first_application.refresh_from_db()
        second_application.refresh_from_db()

        self.assertNotEqual(first_application.academic_year_id, second_application.academic_year_id)
        self.assertEqual(CourseApplication.objects.count(), 2)

    def test_registration_api_creates_credentials_without_returning_password(self):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            response = self.client.post(
                reverse('course_registration_api'),
                data=self.application_form_payload(),
            )

        self.assertEqual(response.status_code, 201)

        payload = response.json()

        self.assertTrue(payload['success'])
        self.assertEqual(payload['status'], CourseApplication.STATUS_CONFIRMED)
        self.assertEqual(payload['status_display'], 'Подтверждена')
        self.assertTrue(payload['credentials_created'])
        self.assertNotIn('login', payload)
        self.assertNotIn('temporary_password', payload)

    def test_registration_api_rejects_non_object_json_payload(self):
        response = self.client.post(
            reverse('course_registration_api'),
            data='[]',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'success': False, 'message': 'Неверный формат запроса.'},
        )
        self.assertFalse(CourseApplication.objects.exists())

    def test_registration_api_rejects_invalid_utf8_json_payload(self):
        response = self.client.post(
            reverse('course_registration_api'),
            data=b'{"student_phone": "\xff"}',
            content_type='application/json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(
            response.json(),
            {'success': False, 'message': 'Неверный формат запроса.'},
        )
        self.assertFalse(CourseApplication.objects.exists())

    def test_registration_api_requires_csrf_cookie(self):
        csrf_client = Client(enforce_csrf_checks=True, HTTP_HOST='127.0.0.1')

        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            response = csrf_client.post(
                reverse('course_registration_api'),
                data=self.application_form_payload(),
            )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(CourseApplication.objects.count(), 0)

    def test_registration_api_rejects_duplicate_phone(self):
        CourseApplication.objects.create(**self.application_payload())

        response = self.client.post(
            reverse('course_registration_api'),
            data=self.application_form_payload(
                last_name='Петров',
                first_name='Пётр',
                student_phone='8 999 123 45 67',
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()['success'])
        self.assertIn('student_phone', response.json()['errors'])
        self.assertEqual(CourseApplication.objects.count(), 1)

    @override_settings(
        CACHES={
            'default': {
                'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
                'LOCATION': 'course-registration-throttle-test',
            },
        },
    )
    def test_registration_api_limits_repeated_requests_from_same_ip(self):
        url = reverse('course_registration_api')

        for _ in range(10):
            response = self.client.post(
                url,
                data='{}',
                content_type='application/json',
                REMOTE_ADDR='203.0.113.10',
            )
            self.assertEqual(response.status_code, 400)

        response = self.client.post(
            url,
            data='{}',
            content_type='application/json',
            REMOTE_ADDR='203.0.113.10',
        )

        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json()['success'])

    @override_settings(TRUST_X_FORWARDED_FOR=True, TRUSTED_PROXY_COUNT=1)
    def test_registration_rate_limit_uses_ip_appended_by_trusted_proxy(self):
        url = reverse('course_registration_api')

        for attempt in range(10):
            response = self.client.post(
                url,
                data='{}',
                content_type='application/json',
                REMOTE_ADDR='172.18.0.1',
                HTTP_X_FORWARDED_FOR=f'198.51.100.{attempt}, 203.0.113.10',
            )
            self.assertEqual(response.status_code, 400)

        response = self.client.post(
            url,
            data='{}',
            content_type='application/json',
            REMOTE_ADDR='172.18.0.1',
            HTTP_X_FORWARDED_FOR='198.51.100.250, 203.0.113.10',
        )

        self.assertEqual(response.status_code, 429)
        self.assertFalse(response.json()['success'])


class AsyncDatabaseViewTests(TestCase):
    def test_healthcheck_verifies_database_connection(self):
        response = self.client.get(reverse('healthcheck'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_database_backed_url_views_are_async(self):
        from asgiref.sync import iscoroutinefunction

        from journal import admin_tools, views

        async_views = (
            views.password_help_view,
            views.grade_options_api,
            views.assignment_options_api,
            views.journal_view,
            views.course_registration_view,
            views.course_registration_api,
            views.export_student_credentials_xlsx,
            views.export_all_data_excel,
            admin_tools.admin_data_tools_view,
            admin_tools.admin_guide_view,
            admin_tools.admin_seed_test_data_view,
            admin_tools.admin_delete_database_view,
            admin_tools.admin_export_test_credentials_excel_view,
        )

        for view_func in async_views:
            with self.subTest(view=view_func.__name__):
                self.assertTrue(iscoroutinefunction(view_func))


class ExportTemporaryCredentialsAdminXlsxTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='admin_xlsx',
            password='Pass12345!',
            email='admin-xlsx@example.com',
        )
        self.regular_user = User.objects.create_user(
            username='regular_xlsx',
            password='Pass12345!',
        )
        self.staff_user = User.objects.create_user(
            username='staff_xlsx',
            password='Pass12345!',
            is_staff=True,
        )

        self.teacher_group = Group.objects.create(name='Преподаватель')
        self.student_group = Group.objects.create(name='Ученик')

        self.teacher_user = User.objects.create_user(
            username='teacher_export',
            password='Pass12345!',
        )
        self.teacher_user.groups.add(self.teacher_group)

        self.student_user = User.objects.create_user(
            username='student_export',
            password='Pass12345!',
        )
        self.student_user.groups.add(self.student_group)

        TemporaryCredential.objects.create(
            login='teacher_export',
            temporary_password='TeacherTemp123!',
        )
        TemporaryCredential.objects.create(
            login='student_export',
            temporary_password='StudentTemp123!',
        )

    def test_superuser_can_download_temporary_credentials_xlsx(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_test_credentials_excel'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

        workbook = load_workbook(BytesIO(response.content))
        worksheet = workbook.active
        rows = list(worksheet.iter_rows(values_only=True))

        self.assertEqual(rows[0], ('Логин', 'Пароль', 'Роль'))
        self.assertIn(('teacher_export', 'TeacherTemp123!', 'Преподаватель'), rows)
        self.assertIn(('student_export', 'StudentTemp123!', 'Ученик'), rows)
        self.assertNotIn('Телефон ученика', rows[0])
        self.assertNotIn('Заявка', rows[0])

    def test_admin_temporary_credentials_export_rejects_post(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(reverse('admin_export_test_credentials_excel'))

        self.assertEqual(response.status_code, 405)

    def test_temporary_credentials_export_escapes_excel_formulas(self):
        TemporaryCredential.objects.create(
            login='=HYPERLINK("https://example.invalid")',
            temporary_password='+1+1',
        )
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_test_credentials_excel'))
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        rows = list(workbook.active.iter_rows(values_only=True))

        self.assertIn(
            ("'=HYPERLINK(\"https://example.invalid\")", "'+1+1", None),
            rows,
        )

    def test_temporary_credentials_export_rejects_post(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(reverse('export_student_credentials_xlsx'))

        self.assertEqual(response.status_code, 405)

    def test_regular_user_cannot_download_temporary_credentials_xlsx(self):
        self.client.login(username='regular_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_test_credentials_excel'))

        self.assertEqual(response.status_code, 302)

    def test_staff_user_cannot_download_temporary_credentials_xlsx(self):
        self.client.login(username='staff_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_test_credentials_excel'))

        self.assertEqual(response.status_code, 302)

    def test_staff_user_cannot_open_data_tools(self):
        self.client.login(username='staff_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_data_tools'))

        self.assertEqual(response.status_code, 302)

    def test_superuser_can_open_data_tools_with_delete_database_button(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_data_tools'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Удалить базу данных')
        self.assertContains(response, 'name="pas_key_data"')
        self.assertContains(response, reverse('admin_guide'))

    def test_staff_user_cannot_open_seed_test_data_tool(self):
        self.client.login(username='staff_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_seed_test_data'))

        self.assertEqual(response.status_code, 302)

    def test_staff_user_cannot_delete_database(self):
        self.client.login(username='staff_xlsx', password='Pass12345!')

        response = self.client.post(
            reverse('admin_delete_database'),
            data={
                'confirm_delete': 'yes',
                'pas_key_data': 'rtycds28',
            },
        )

        self.assertEqual(response.status_code, 302)

    @patch('journal.admin_tools.call_command')
    def test_superuser_can_open_seed_test_data_tool_without_running_it(self, mocked_call_command):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_seed_test_data'))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Запуск тестовых данных')
        self.assertContains(response, 'Подтверждаю пересоздание тестовых данных')
        self.assertContains(response, 'name="pas_key_data"')
        mocked_call_command.assert_not_called()

    @override_settings(DATA_TOOLS_PASSWORD='rtycds28')
    @patch('journal.admin_tools.call_command')
    def test_superuser_can_run_seed_test_data_tool(self, mocked_call_command):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(
            reverse('admin_seed_test_data'),
            data={
                'confirm': 'yes',
                'pas_key_data': 'rtycds28',
            },
        )

        self.assertEqual(response.status_code, 302)
        mocked_call_command.assert_called_once_with('seed_data')

    @override_settings(DATA_TOOLS_PASSWORD='rtycds28')
    @patch('journal.admin_tools.call_command')
    def test_seed_test_data_tool_rejects_wrong_password(self, mocked_call_command):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(
            reverse('admin_seed_test_data'),
            data={
                'confirm': 'yes',
                'pas_key_data': 'wrong',
            },
        )

        self.assertEqual(response.status_code, 302)
        mocked_call_command.assert_not_called()

    @override_settings(DATA_TOOLS_PASSWORD='rtycds28')
    def test_superuser_can_delete_database_with_confirmation_password(self):
        self.create_base_journal()
        CourseRegistrationSettings.objects.update_or_create(
            pk=1,
            defaults={'telegram_group_url': 'https://t.me/test_group'},
        )
        PasswordRecoveryContact.objects.create(
            name='Администратор',
            phone='+7 (999) 123-45-67',
            messengers='Telegram',
        )
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(
            reverse('admin_delete_database'),
            data={
                'confirm_delete': 'yes',
                'pas_key_data': 'rtycds28',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(AcademicYear.objects.exists())
        self.assertFalse(Instrument.objects.exists())
        self.assertFalse(Subject.objects.exists())
        self.assertFalse(Teacher.objects.exists())
        self.assertFalse(Student.objects.exists())
        self.assertFalse(GroupSubject.objects.exists())
        self.assertFalse(StudentSubject.objects.exists())
        self.assertFalse(Grade.objects.exists())
        self.assertFalse(SubjectResult.objects.exists())
        self.assertFalse(CourseApplication.objects.exists())
        self.assertFalse(CourseRegistrationSettings.objects.exists())
        self.assertFalse(PasswordRecoveryContact.objects.exists())
        self.assertFalse(TemporaryCredential.objects.exists())
        self.assertTrue(User.objects.filter(pk=self.admin_user.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.staff_user.pk).exists())
        self.assertFalse(User.objects.filter(pk=self.regular_user.pk).exists())

    @override_settings(DATA_TOOLS_PASSWORD='rtycds28')
    def test_delete_database_rejects_wrong_password(self):
        self.create_base_journal()
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(
            reverse('admin_delete_database'),
            data={
                'confirm_delete': 'yes',
                'pas_key_data': 'wrong',
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Student.objects.exists())

    def test_staff_user_cannot_download_full_export(self):
        self.client.login(username='staff_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_all_data_excel'))

        self.assertEqual(response.status_code, 302)

    def test_superuser_can_download_full_export(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_all_data_excel'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )

    def test_full_export_rejects_post(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.post(reverse('admin_export_all_data_excel'))

        self.assertEqual(response.status_code, 405)

    def test_full_export_escapes_excel_formulas(self):
        Instrument.objects.create(name='=1+1')
        self.client.login(username='admin_xlsx', password='Pass12345!')

        response = self.client.get(reverse('admin_export_all_data_excel'))
        workbook = load_workbook(BytesIO(response.content), data_only=False)
        instrument_values = [
            cell.value
            for row in workbook['Инструменты'].iter_rows()
            for cell in row
        ]

        self.assertIn("'=1+1", instrument_values)

    @override_settings(ENABLE_DESTRUCTIVE_DATA_TOOLS=False)
    def test_destructive_data_tools_are_hidden_and_forbidden_when_disabled(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')

        tools_response = self.client.get(reverse('admin_data_tools'))
        seed_response = self.client.get(reverse('admin_seed_test_data'))
        delete_response = self.client.post(
            reverse('admin_delete_database'),
            data={
                'confirm_delete': 'yes',
                'pas_key_data': 'rtycds28',
            },
        )

        self.assertNotContains(tools_response, 'Запуск тестовых данных')
        self.assertNotContains(tools_response, 'Удалить базу данных')
        self.assertEqual(seed_response.status_code, 403)
        self.assertEqual(delete_response.status_code, 403)


class AccountUtilityTests(JournalTestDataMixin, TestCase):
    def test_build_username_helpers_use_name_and_surname(self):
        self.assertEqual(build_display_name_from_full_name('Иван Иванов'), 'Иванов Иван')
        self.assertEqual(build_username_from_full_name('Иван Иванов'), 'Иванов Иван')
        self.assertEqual(build_course_application_login('Иванов', 'Иван'), 'Иванов Иван')

    def test_temporary_passwords_are_short_and_easy_to_type(self):
        password = generate_temporary_password()

        self.assertEqual(len(password), 8)
        self.assertLessEqual(
            set(password),
            set('abcdefghjkmnpqrstuvwxyz23456789'),
        )

    def test_display_name_for_user_prefers_student_profile(self):
        group = self.create_group()
        instrument = self.create_instrument()
        user = User.objects.create_user(
            username='tempuser',
            password='Pass12345!',
            first_name='Иван',
            last_name='Иванов',
        )

        Student.objects.create(
            full_name='Иван Иванов',
            group=group,
            instrument=instrument,
            user=user,
        )

        self.assertEqual(display_name_for_user(user), 'Иванов Иван')

    def test_display_name_for_user_prefers_teacher_profile(self):
        user = User.objects.create_user(
            username='teacher_user',
            password='Pass12345!',
            first_name='Иван',
            last_name='Иванов',
        )

        Teacher.objects.create(full_name='Пётр Петров', user=user)

        self.assertEqual(display_name_for_user(user), 'Петров Пётр')

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_user_creation_form_accepts_username_with_space(self):
        form = UserCreationForm(
            data={
                'username': 'Админ Тест',
                'password1': 'Pass12345!',
                'password2': 'Pass12345!',
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        user = form.save()
        user.full_clean()
        self.assertEqual(user.username, 'Админ Тест')

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_user_creation_form_rejects_control_characters_in_username(self):
        form = UserCreationForm(
            data={
                'username': 'Админ\tТест',
                'password1': 'Pass12345!',
                'password2': 'Pass12345!',
            },
        )

        self.assertFalse(form.is_valid())
        self.assertIn('username', form.errors)


class AccountCommandTests(JournalTestDataMixin, TestCase):
    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_create_student_accounts_stores_actual_unique_usernames(self):
        group = self.create_group()
        instrument = self.create_instrument()
        Student.objects.create(
            full_name='Иван Иванов',
            group=group,
            instrument=instrument,
        )
        Student.objects.create(
            full_name='Иван Иванов',
            group=group,
            instrument=instrument,
        )

        call_command('create_student_accounts', stdout=StringIO())

        self.assertEqual(
            list(User.objects.order_by('username').values_list('username', flat=True)),
            ['Иванов Иван', 'Иванов Иван 2'],
        )
        self.assertEqual(
            list(TemporaryCredential.objects.order_by('login').values_list('login', flat=True)),
            ['Иванов Иван', 'Иванов Иван 2'],
        )
        self.assertFalse(TemporaryCredential.objects.filter(user__isnull=True).exists())

        call_command('create_student_accounts', stdout=StringIO())

        self.assertEqual(TemporaryCredential.objects.count(), 2)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_create_student_accounts_preserves_existing_password_without_credential(self):
        user = User.objects.create_user(
            username='existing student',
            password='ExistingPass123!',
        )
        original_password_hash = user.password
        Student.objects.create(
            full_name='Существующий Ученик',
            group=self.create_group(),
            instrument=self.create_instrument(),
            user=user,
        )

        call_command('create_student_accounts', stdout=StringIO())
        user.refresh_from_db()

        self.assertEqual(user.password, original_password_hash)
        self.assertFalse(TemporaryCredential.objects.filter(user=user).exists())

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_create_teacher_accounts_stores_actual_unique_usernames(self):
        Teacher.objects.create(full_name='Иван Иванов')
        Teacher.objects.create(full_name='Иван Иванов')

        call_command('create_teacher_accounts', stdout=StringIO())

        self.assertEqual(
            list(User.objects.order_by('username').values_list('username', flat=True)),
            ['Иванов Иван', 'Иванов Иван 2'],
        )
        self.assertEqual(
            list(TemporaryCredential.objects.order_by('login').values_list('login', flat=True)),
            ['Иванов Иван', 'Иванов Иван 2'],
        )
        self.assertFalse(TemporaryCredential.objects.filter(user__isnull=True).exists())

        call_command('create_teacher_accounts', stdout=StringIO())

        self.assertEqual(TemporaryCredential.objects.count(), 2)

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_create_teacher_accounts_preserves_existing_password_without_credential(self):
        user = User.objects.create_user(
            username='existing teacher',
            password='ExistingPass123!',
        )
        original_password_hash = user.password
        Teacher.objects.create(
            full_name='Существующий Преподаватель',
            user=user,
        )

        call_command('create_teacher_accounts', stdout=StringIO())
        user.refresh_from_db()

        self.assertEqual(user.password, original_password_hash)
        self.assertFalse(TemporaryCredential.objects.filter(user=user).exists())

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_temporary_password_cannot_be_reassigned_to_existing_user(self):
        user = User.objects.create_user(
            username='immutable temporary password',
            password='InitialPass123!',
        )
        credential = TemporaryCredential.objects.create(
            user=user,
            login=user.username,
            temporary_password='InitialPass123!',
        )
        original_password_hash = user.password

        with self.assertRaisesRegex(
            ValueError,
            'only be stored when a new user is created',
        ):
            ensure_temporary_credential_for_user(
                user,
                password='ReplacementPass123!',
            )

        user.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(user.password, original_password_hash)
        self.assertEqual(credential.temporary_password, 'InitialPass123!')
        self.assertTrue(user.check_password('InitialPass123!'))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_createsuperuser_rolls_back_if_credentials_cannot_be_stored(self):
        with (
            patch.dict(
                'os.environ',
                {'DJANGO_SUPERUSER_PASSWORD': 'AdminTemp123!'},
            ),
            patch(
                'journal.command_overrides.management.commands.createsuperuser.'
                'ensure_temporary_credential_for_user',
                side_effect=RuntimeError('credential failure'),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, 'credential failure'):
                call_command(
                    'createsuperuser',
                    interactive=False,
                    username='rolled back admin',
                    email='rollback@example.com',
                    stdout=StringIO(),
                )

        self.assertFalse(User.objects.filter(username='rolled back admin').exists())
        self.assertFalse(TemporaryCredential.objects.filter(login='rolled back admin').exists())

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_createsuperuser_stores_temporary_credentials(self):
        with patch.dict(
            'os.environ',
            {'DJANGO_SUPERUSER_PASSWORD': 'AdminTemp123!'},
        ):
            call_command(
                'createsuperuser',
                interactive=False,
                username='created admin',
                email='created-admin@example.com',
                stdout=StringIO(),
            )

        user = User.objects.get(username='created admin')
        credential = TemporaryCredential.objects.get(login='created admin')

        self.assertTrue(user.is_superuser)
        self.assertTrue(user.groups.filter(name='Администратор').exists())
        self.assertEqual(credential.user, user)
        self.assertEqual(credential.temporary_password, 'AdminTemp123!')
        self.assertTrue(user.check_password(credential.temporary_password))


class UserCreationCredentialTests(JournalTestDataMixin, TestCase):
    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_user_admin_creation_stores_temporary_credentials(self):
        admin_user = User.objects.create_superuser(
            username='admin creator',
            password='Pass12345!',
        )
        request = type('Request', (), {'user': admin_user})()
        model_admin = django_admin.site._registry[User]
        form = model_admin.add_form(data={
            'username': 'created in admin',
            'password1': 'AdminCreated123!',
            'password2': 'AdminCreated123!',
        })
        self.assertTrue(form.is_valid(), form.errors)
        user = form.save(commit=False)

        model_admin.save_model(request, user, form, change=False)

        credential = TemporaryCredential.objects.get(user=user)
        self.assertEqual(credential.login, 'created in admin')
        self.assertEqual(credential.temporary_password, 'AdminCreated123!')
        self.assertTrue(user.check_password(credential.temporary_password))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_user_admin_update_preserves_password_and_syncs_existing_credential(self):
        admin_user = User.objects.create_superuser(
            username='admin editor',
            password='Pass12345!',
        )
        request = type('Request', (), {'user': admin_user})()
        model_admin = django_admin.site._registry[User]
        user = User.objects.create_user(
            username='before edit',
            password='ExistingPass123!',
        )
        credential = TemporaryCredential.objects.create(
            user=user,
            login=user.username,
            temporary_password='ExistingPass123!',
        )
        original_password_hash = user.password

        user.username = 'after edit'
        user.email = 'updated@example.com'
        model_admin.save_model(request, user, form=None, change=True)

        user.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(user.password, original_password_hash)
        self.assertEqual(credential.login, 'after edit')
        self.assertEqual(credential.temporary_password, 'ExistingPass123!')
        self.assertTrue(user.check_password('ExistingPass123!'))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_user_admin_update_does_not_create_temporary_password(self):
        admin_user = User.objects.create_superuser(
            username='admin editor without credential',
            password='Pass12345!',
        )
        request = type('Request', (), {'user': admin_user})()
        model_admin = django_admin.site._registry[User]
        user = User.objects.create_user(
            username='regular account',
            password='ExistingPass123!',
        )
        original_password_hash = user.password

        user.email = 'regular-updated@example.com'
        model_admin.save_model(request, user, form=None, change=True)

        user.refresh_from_db()
        self.assertEqual(user.password, original_password_hash)
        self.assertFalse(TemporaryCredential.objects.filter(user=user).exists())
        self.assertTrue(user.check_password('ExistingPass123!'))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_ensure_superuser_creation_stores_temporary_credentials(self):
        env = {
            'DJANGO_SUPERUSER_USERNAME': 'container admin',
            'DJANGO_SUPERUSER_EMAIL': 'container-admin@example.com',
            'DJANGO_SUPERUSER_PASSWORD': 'ContainerAdmin123!',
        }
        with patch.dict('os.environ', env, clear=False):
            call_command('ensure_superuser', stdout=StringIO())

        user = User.objects.get(username='container admin')
        credential = TemporaryCredential.objects.get(user=user)
        self.assertTrue(user.is_superuser)
        self.assertEqual(credential.temporary_password, 'ContainerAdmin123!')
        self.assertTrue(user.check_password(credential.temporary_password))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_ensure_superuser_update_preserves_password_without_rotation(self):
        user = User.objects.create_superuser(
            username='existing managed admin',
            email='old@example.com',
            password='ActualAdmin123!',
        )
        credential = TemporaryCredential.objects.create(
            user=user,
            login=user.username,
            temporary_password='ActualAdmin123!',
        )
        original_password_hash = user.password
        env = {
            'DJANGO_SUPERUSER_USERNAME': user.username,
            'DJANGO_SUPERUSER_EMAIL': 'new@example.com',
            'DJANGO_SUPERUSER_PASSWORD': 'DifferentConfiguredPassword123!',
        }

        with patch.dict('os.environ', env, clear=False):
            call_command('ensure_superuser', stdout=StringIO())

        user.refresh_from_db()
        credential.refresh_from_db()
        self.assertEqual(user.email, 'new@example.com')
        self.assertEqual(user.password, original_password_hash)
        self.assertEqual(credential.temporary_password, 'ActualAdmin123!')
        self.assertTrue(user.check_password('ActualAdmin123!'))
        self.assertFalse(user.check_password('DifferentConfiguredPassword123!'))

    @override_settings(AUTH_PASSWORD_VALIDATORS=[])
    def test_ensure_superuser_does_not_create_credentials_for_existing_user(self):
        user = User.objects.create_superuser(
            username='existing container admin',
            password='ActualAdmin123!',
        )
        original_password_hash = user.password
        env = {
            'DJANGO_SUPERUSER_USERNAME': user.username,
            'DJANGO_SUPERUSER_EMAIL': 'updated-admin@example.com',
            'DJANGO_SUPERUSER_PASSWORD': 'DifferentConfiguredPassword123!',
        }

        with patch.dict('os.environ', env, clear=False):
            call_command('ensure_superuser', stdout=StringIO())

        user.refresh_from_db()
        self.assertEqual(user.email, 'updated-admin@example.com')
        self.assertEqual(user.password, original_password_hash)
        self.assertFalse(TemporaryCredential.objects.filter(user=user).exists())
        self.assertTrue(user.check_password('ActualAdmin123!'))



class SeedDataCommandTests(TestCase):
    @staticmethod
    def run_seed_data():
        with TemporaryDirectory() as tmp_dir:
            credentials_path = Path(tmp_dir) / 'secrets.csv'
            call_command(
                'seed_data',
                credentials_output=str(credentials_path),
                stdout=StringIO(),
            )

    @classmethod
    def setUpTestData(cls):
        cls.run_seed_data()

    def test_seed_data_creates_new_architecture_records(self):
        self.assertTrue(CourseRegistrationSettings.objects.filter(pk=1).exists())
        self.assertEqual(PasswordRecoveryContact.objects.count(), 2)
        self.assertTrue(AcademicYear.objects.exists())
        self.assertEqual(AcademicYear.objects.count(), 1)
        self.assertTrue(
            AcademicYear.objects.filter(
                name='2025/2026',
                starts_on=date(2025, 9, 1),
                ends_on=date(2026, 8, 31),
                is_active=True,
            ).exists(),
        )
        self.assertTrue(Instrument.objects.exists())
        self.assertTrue(StudyGroup.objects.exists())
        self.assertTrue(Subject.objects.exists())
        self.assertTrue(Teacher.objects.exists())
        self.assertTrue(Student.objects.exists())
        self.assertTrue(GroupSubject.objects.exists())
        self.assertTrue(StudentSubject.objects.exists())
        self.assertTrue(Grade.objects.exists())
        self.assertTrue(SubjectResult.objects.exists())
        self.assertTrue(CourseApplication.objects.exists())

    def test_seed_data_assigns_user_roles(self):
        self.assertTrue(Group.objects.filter(name='Администратор').exists())
        self.assertTrue(Group.objects.filter(name='Преподаватель').exists())
        self.assertTrue(Group.objects.filter(name='Ученик').exists())

        teacher = Teacher.objects.select_related('user').first()
        student = Student.objects.select_related('user').first()

        self.assertIsNotNone(teacher)
        self.assertIsNotNone(student)

        self.assertTrue(
            teacher.user.groups.filter(name='Преподаватель').exists(),
        )
        self.assertTrue(
            student.user.groups.filter(name='Ученик').exists(),
        )

    def test_seed_data_preserves_existing_admin_password_without_fabricating_credential(self):
        admin_user = User.objects.create_superuser(
            username='existing_admin',
            password='OriginalPass123!',
            email='existing-admin@example.com',
        )

        self.run_seed_data()

        admin_user.refresh_from_db()

        self.assertTrue(User.objects.filter(username='existing_admin').exists())
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)
        self.assertTrue(
            admin_user.groups.filter(name='Администратор').exists(),
        )
        self.assertTrue(admin_user.check_password('OriginalPass123!'))
        self.assertFalse(TemporaryCredential.objects.filter(user=admin_user).exists())

    @override_settings(IS_PRODUCTION_ENV=True)
    def test_seed_data_is_blocked_in_production_without_explicit_override(self):
        students_before = Student.objects.count()

        with TemporaryDirectory() as tmp_dir:
            with self.assertRaisesMessage(CommandError, 'запрещена в production'):
                call_command(
                    'seed_data',
                    credentials_output=str(Path(tmp_dir) / 'secrets.csv'),
                    stdout=StringIO(),
                )

        self.assertEqual(Student.objects.count(), students_before)

    def test_seed_data_creates_temporary_credentials_for_every_user(self):
        user_logins = set(User.objects.values_list('username', flat=True))
        credential_logins = set(TemporaryCredential.objects.values_list('login', flat=True))
        credential_user_ids = set(TemporaryCredential.objects.values_list('user_id', flat=True))

        self.assertEqual(credential_logins, user_logins)
        self.assertEqual(credential_user_ids, set(User.objects.values_list('id', flat=True)))

    def test_seed_data_has_no_assignment_or_grade_contradictions(self):
        active_year = AcademicYear.objects.get(is_active=True)

        self.assertFalse(StudyGroup.objects.exclude(academic_year=active_year).exists())
        self.assertFalse(CourseApplication.objects.exclude(academic_year=active_year).exists())
        self.assertFalse(Grade.objects.exclude(academic_year=active_year).exists())
        self.assertFalse(SubjectResult.objects.exclude(academic_year=active_year).exists())
        self.assertFalse(
            Grade.objects.filter(
                Q(date__lt=active_year.starts_on) | Q(date__gt=active_year.ends_on),
            ).exists(),
        )

        self.assertFalse(GroupSubject.objects.filter(subject__is_specialty=True).exists())
        self.assertFalse(StudentSubject.objects.filter(subject__is_specialty=False).exists())
        self.assertFalse(
            StudentSubject.objects
            .filter(is_specialty=True)
            .exclude(subject__name='Специальность')
            .exists(),
        )
        self.assertFalse(
            StudentSubject.objects
            .filter(is_specialty=False, subject__name='Специальность')
            .exists(),
        )
        self.assertFalse(
            Student.objects
            .filter(is_active=True)
            .annotate(
                active_specialties=Count(
                    'individual_subjects',
                    filter=Q(individual_subjects__is_active=True, individual_subjects__is_specialty=True),
                ),
            )
            .exclude(active_specialties=1)
            .exists(),
        )

        group_grade_keys = set(
            GroupSubject.objects
            .filter(is_active=True)
            .values_list('group_id', 'subject_id', 'teacher_id')
        )
        individual_grade_keys = set(
            StudentSubject.objects
            .filter(is_active=True)
            .values_list('student_id', 'subject_id', 'teacher_id')
        )
        invalid_grade_ids = [
            grade_id
            for grade_id, student_id, group_id, subject_id, teacher_id in Grade.objects.values_list(
                'pk',
                'student_id',
                'student__group_id',
                'subject_id',
                'teacher_id',
            )
            if (
                (group_id, subject_id, teacher_id) not in group_grade_keys
                and (student_id, subject_id, teacher_id) not in individual_grade_keys
            )
        ]
        self.assertEqual(invalid_grade_ids, [])

        group_result_keys = set(
            GroupSubject.objects
            .filter(is_active=True)
            .values_list('group_id', 'subject_id')
        )
        individual_result_keys = set(
            StudentSubject.objects
            .filter(is_active=True)
            .values_list('student_id', 'subject_id')
        )
        invalid_result_ids = [
            result_id
            for result_id, student_id, group_id, subject_id in SubjectResult.objects.values_list(
                'pk',
                'student_id',
                'student__group_id',
                'subject_id',
            )
            if (group_id, subject_id) not in group_result_keys and (student_id, subject_id) not in individual_result_keys
        ]
        self.assertEqual(invalid_result_ids, [])

    def test_seed_data_can_be_run_twice_without_duplicate_settings_error(self):
        self.run_seed_data()

        self.assertEqual(
            CourseRegistrationSettings.objects.filter(pk=1).count(),
            1,
        )
        self.assertTrue(Student.objects.exists())
        self.assertTrue(Teacher.objects.exists())

    def test_seed_data_populates_maximum_demo_profiles(self):
        self.assertGreaterEqual(Instrument.objects.count(), 14)
        self.assertGreaterEqual(Subject.objects.count(), 21)
        self.assertGreaterEqual(StudyGroup.objects.count(), 7)
        self.assertGreaterEqual(Teacher.objects.count(), 9)
        self.assertGreaterEqual(Student.objects.count(), 35)
        self.assertGreaterEqual(GroupSubject.objects.count(), 33)
        self.assertGreaterEqual(StudentSubject.objects.count(), 70)
        self.assertGreaterEqual(StudentSubject.objects.filter(is_specialty=False).count(), 35)
        self.assertFalse(GroupSubject.objects.filter(subject__is_specialty=True).exists())
        self.assertFalse(StudentSubject.objects.filter(subject__is_specialty=False).exists())
        self.assertEqual(Grade.objects.count(), 1542)

        self.assertEqual(
            set(Grade.objects.values_list('value', flat=True)),
            {Grade.GRADE_1, Grade.GRADE_2, Grade.GRADE_3, Grade.GRADE_4, Grade.GRADE_5, Grade.GRADE_ABSENT},
        )
        self.assertFalse(
            Student.objects
            .filter(is_active=True)
            .annotate(grades_count=Count('grades'))
            .filter(grades_count=0)
            .exists(),
        )
        self.assertFalse(Grade.objects.exclude(academic_year__name='2025/2026').exists())
        self.assertFalse(CourseApplication.objects.exclude(academic_year__name='2025/2026').exists())
        self.assertFalse(
            Grade.objects.filter(
                Q(date__lt=date(2025, 9, 1)) | Q(date__gt=date(2026, 8, 31)),
            ).exists(),
        )

        registration_settings = CourseRegistrationSettings.objects.get(pk=1)
        self.assertEqual(
            registration_settings.telegram_group_url,
            'https://t.me/cadet_journal_demo',
        )
        self.assertEqual(registration_settings.minimum_registration_age, 14)
        self.assertFalse(hasattr(registration_settings, 'course_starts_on'))
        self.assertFalse(hasattr(registration_settings, 'course_ends_on'))

        self.assertFalse(Teacher.objects.filter(birth_date__isnull=True).exists())
        for field_name in ('phone', 'email', 'comments'):
            self.assertFalse(Teacher.objects.filter(**{field_name: ''}).exists())

        self.assertFalse(Student.objects.filter(birth_date__isnull=True).exists())
        for field_name in (
            'gender',
            'city_church',
            'music_education',
            'student_phone',
            'parent_contacts',
            'comments',
        ):
            self.assertFalse(Student.objects.filter(**{field_name: ''}).exists())

        self.assertTrue(StudyGroup.objects.filter(is_active=False).exists())
        self.assertTrue(GroupSubject.objects.filter(is_active=False).exists())
        self.assertTrue(StudentSubject.objects.filter(is_active=False).exists())
        self.assertFalse(Grade.objects.filter(comment='').exists())

        self.assertGreaterEqual(
            CourseApplication.objects.filter(
                status=CourseApplication.STATUS_CONFIRMED,
                student__isnull=False,
                user__isnull=False,
            ).count(),
            5,
        )
        self.assertGreaterEqual(
            CourseApplication.objects.filter(
                status=CourseApplication.STATUS_REJECTED,
                student__isnull=True,
                user__isnull=True,
            ).count(),
            2,
        )

        for student in Student.objects.select_related('user'):
            credential = TemporaryCredential.objects.get(login=student.user.username)
            self.assertEqual(credential.user, student.user)
            self.assertEqual(credential.student_phone, student.student_phone)


class ExportCommandsCompatibilityTests(JournalTestDataMixin, TestCase):
    """
    Эти тесты можно оставить, если в проекте сохранены management-команды
    export_temporary_credentials и export_student_credentials_with_phone.

    Если команды удалены, удалите этот класс из tests.py.
    """

    def test_export_temporary_credentials_command_outputs_csv_if_command_exists(
        self,
    ):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            CourseApplication.objects.create(**self.application_payload())

        output = StringIO()

        try:
            call_command('export_temporary_credentials', stdout=output)
        except CommandError:  # pragma: no cover
            self.skipTest(
                'Команда export_temporary_credentials не найдена в проекте.',
            )

        csv_output = output.getvalue()

        self.assertIn('role,name,login,temporary_password,created_at,phone', csv_output)
        self.assertIn('student', csv_output)
        self.assertIn('login', csv_output)
        self.assertIn('temporary_password', csv_output)
        self.assertIn('Иванов Иван', csv_output)
        self.assertIn('Temp12345!', csv_output)

    def test_export_student_credentials_with_phone_command_outputs_csv_if_command_exists(
        self,
    ):
        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            CourseApplication.objects.create(**self.application_payload())

        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / 'export.csv'

            try:
                call_command(
                    'export_student_credentials_with_phone',
                    output=str(output_path),
                )
            except CommandError:  # pragma: no cover
                self.skipTest(
                    'Команда export_student_credentials_with_phone '
                    'не найдена в проекте.',
                )

            csv_output = output_path.read_text(encoding='utf-8')

        self.assertIn('login', csv_output)
        self.assertIn('temporary_password', csv_output)
        self.assertIn('student_phone', csv_output)
        self.assertIn('Иванов Иван', csv_output)
        self.assertIn('Temp12345!', csv_output)
        self.assertIn('+7 (999) 123-45-67', csv_output)
