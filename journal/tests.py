from __future__ import annotations

from datetime import date
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib import admin as django_admin
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db.models import Count
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from openpyxl import load_workbook

from journal.account_utils import (
    build_course_application_login,
    build_display_name_from_full_name,
    build_username_from_full_name,
    display_name_for_user,
    generate_temporary_password,
)
from journal.admin import GradeAdmin, GradeAdminForm, StudentAdminForm, SubjectAdmin, TeacherAdminForm
from journal.forms import (
    CourseApplicationAdminForm,
    CourseApplicationPublicForm,
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
from journal.registration_utils import normalize_parent_contacts
from journal.models import (
    AcademicYear,
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    GroupSubject,
    Instrument,
    PasswordRecoveryContact,
    Student,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TeacherSubject,
    TemporaryCredential,
)


User = get_user_model()


class JournalTestDataMixin:
    """Фабрики для тестов новой архитектуры журнала."""

    def create_academic_year(self, *, name='2025/2026', is_active=True):
        return AcademicYear.objects.create(
            name=name,
            starts_on=date(2025, 9, 1),
            ends_on=date(2026, 8, 31),
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

    def test_group_subject_links_group_subject_and_teacher(self):
        data = self.create_base_journal()

        assignment = GroupSubject.objects.get(
            group=data['group'],
            subject=data['solfeggio'],
        )

        self.assertEqual(assignment.teacher, data['teacher'])
        self.assertIn(data['solfeggio'], data['group'].subjects.all())
        self.assertEqual(data['teacher'].group_subjects.count(), 1)

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

    def test_student_subject_accepts_specialty_subject(self):
        data = self.create_base_journal()
        student = data['student']

        self.assertEqual(student.specialty_subject, data['specialty'])
        self.assertEqual(student.specialty_teacher, data['other_teacher'])
        self.assertIn('Специальность', student.subjects_display)

    def test_student_subject_rejects_non_specialty_when_marked_as_specialty(self):
        data = self.create_base_journal()

        with self.assertRaises(ValidationError):
            StudentSubject.objects.create(
                student=data['student'],
                subject=data['solfeggio'],
                teacher=data['teacher'],
                is_specialty=True,
            )

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

    def test_parent_contacts_accepts_dash_from_form_placeholder(self):
        normalized_contacts = normalize_parent_contacts(
            'Иванов Иван Иванович — +7 (999) 123-45-67',
        )

        self.assertEqual(
            normalized_contacts,
            'Иванов Иван Иванович - +7 (999) 123-45-67',
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
        self.assertIn('Ученик не состоит в выбранной группе.', str(form.errors))

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
        self.assertIn('Ученик не может получить оценку', str(form.errors))

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

    def test_user_with_temporary_password_is_redirected_to_password_change(self):
        TemporaryCredential.objects.create(
            login=self.data['teacher'].user.username,
            temporary_password='abc234de',
        )
        self.client.login(username='teacher_ivanov', password='Pass12345!')

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
            login=self.data['teacher'].user.username,
            temporary_password='abc234de',
        )
        self.client.login(username='teacher_ivanov', password='Pass12345!')

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
                login=self.data['teacher'].user.username,
            ).exists(),
        )

        self.client.logout()

        self.assertTrue(
            self.client.login(
                username='teacher_ivanov',
                password='NewPass12345!',
            ),
        )


class AdminDashboardTests(JournalTestDataMixin, TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='dashboard_admin',
            password='Pass12345!',
            email='dashboard-admin@example.com',
        )

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

    def test_related_models_are_visible_in_admin_and_subject_contains_all_relation_inlines(self):
        request = type('Request', (), {'user': self.admin_user})()

        for model in (GroupSubject, StudentSubject, TeacherSubject):
            with self.subTest(model=model.__name__):
                model_admin = django_admin.site._registry[model]
                self.assertTrue(model_admin.get_model_perms(request).get('view'))

        self.assertEqual(
            [inline.model for inline in SubjectAdmin.inlines],
            [TeacherSubject, GroupSubject, StudentSubject],
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
        CourseRegistrationSettings.objects.create(
            pk=1,
            telegram_group_url='https://t.me/test_group',
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

    def test_registration_api_returns_generated_credentials(self):
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
        self.assertEqual(payload['login'], 'Иванов Иван')
        self.assertEqual(payload['temporary_password'], 'Temp12345!')

    def test_registration_api_accepts_public_post_without_csrf_cookie(self):
        csrf_client = Client(enforce_csrf_checks=True, HTTP_HOST='127.0.0.1')

        with patch(
            'journal.account_utils.generate_temporary_password',
            return_value='Temp12345!',
        ):
            response = csrf_client.post(
                reverse('course_registration_api'),
                data=self.application_form_payload(),
            )

        self.assertEqual(response.status_code, 201)
        self.assertTrue(response.json()['success'])

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
        CourseRegistrationSettings.objects.create(
            pk=1,
            telegram_group_url='https://t.me/test_group',
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

        call_command('create_student_accounts', stdout=StringIO())

        self.assertEqual(TemporaryCredential.objects.count(), 2)

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

        call_command('create_teacher_accounts', stdout=StringIO())

        self.assertEqual(TemporaryCredential.objects.count(), 2)


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

    def test_seed_data_preserves_existing_admin_credentials(self):
        admin_user = User.objects.create_superuser(
            username='existing_admin',
            password='OriginalPass123!',
            email='existing-admin@example.com',
        )

        self.run_seed_data()

        admin_user.refresh_from_db()

        self.assertTrue(User.objects.filter(username='existing_admin').exists())
        self.assertTrue(admin_user.check_password('OriginalPass123!'))
        self.assertTrue(admin_user.is_staff)
        self.assertTrue(admin_user.is_superuser)
        self.assertTrue(
            admin_user.groups.filter(name='Администратор').exists(),
        )

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
        self.assertGreaterEqual(Subject.objects.count(), 15)
        self.assertGreaterEqual(StudyGroup.objects.count(), 7)
        self.assertGreaterEqual(Teacher.objects.count(), 9)
        self.assertGreaterEqual(Student.objects.count(), 35)
        self.assertGreaterEqual(GroupSubject.objects.count(), 33)
        self.assertGreaterEqual(StudentSubject.objects.count(), 70)
        self.assertGreaterEqual(StudentSubject.objects.filter(is_specialty=False).count(), 35)
        self.assertEqual(Grade.objects.count(), 1482)

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

        registration_settings = CourseRegistrationSettings.objects.get(pk=1)
        self.assertEqual(
            registration_settings.telegram_group_url,
            'https://t.me/cadet_journal_demo',
        )

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
        except Exception as exc:  # pragma: no cover
            if exc.__class__.__name__ == 'CommandError':
                self.skipTest(
                    'Команда export_temporary_credentials не найдена в проекте.',
                )
            raise

        csv_output = output.getvalue()

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
            except Exception as exc:  # pragma: no cover
                if exc.__class__.__name__ == 'CommandError':
                    self.skipTest(
                        'Команда export_student_credentials_with_phone '
                        'не найдена в проекте.',
                    )
                raise

            csv_output = output_path.read_text(encoding='utf-8')

        self.assertIn('login', csv_output)
        self.assertIn('temporary_password', csv_output)
        self.assertIn('student_phone', csv_output)
        self.assertIn('Иванов Иван', csv_output)
        self.assertIn('Temp12345!', csv_output)
        self.assertIn('+7 (999) 123-45-67', csv_output)
