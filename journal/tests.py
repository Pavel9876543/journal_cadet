from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from journal.account_utils import build_username_from_full_name, display_name_for_user
from journal.forms import CourseApplicationPublicForm
from journal.models import Grade, Group, Student, Subject, Teacher


class JournalAccessTests(TestCase):
    def setUp(self):
        self.subject = Subject.objects.create(name="Сольфеджио")
        self.group = Group.objects.create(name="Группа Тест")
        self.group.subjects.add(self.subject)

        self.teacher_user = User.objects.create_user(username="teacher_test", password="Pass12345!")
        self.teacher = Teacher.objects.create(full_name="Тестовый Преподаватель", user=self.teacher_user)
        self.teacher.subjects.add(self.subject)

        self.student_user = User.objects.create_user(username="student_test", password="Pass12345!")
        self.student = Student.objects.create(
            full_name="Тестовый Ученик",
            group=self.group,
            user=self.student_user,
        )

        self.admin_user = User.objects.create_superuser(
            username="admin_test",
            password="Pass12345!",
            email="admin@example.com",
        )

    def test_teacher_can_open_journal(self):
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.get(reverse("journal"), {"group": self.group.id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Вы вошли как: Тестовый Преподаватель")
        self.assertContains(response, reverse("password_change"))

    def test_authenticated_user_can_open_password_change_page(self):
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)

    def test_student_cannot_edit_inline(self):
        self.client.login(username="student_test", password="Pass12345!")
        response = self.client.post(
            reverse("journal"),
            data={"action": "inline_edit", "grade__1__1__2026-05-15": "5"},
        )
        self.assertEqual(response.status_code, 302)

    def test_teacher_can_edit_own_grade_inline(self):
        grade = Grade.objects.create(
            student=self.student,
            subject=self.subject,
            teacher=self.teacher,
            date="2026-05-15",
            value="3",
        )
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.post(
            f"{reverse('journal')}?group={self.group.id}",
            data={"action": "inline_edit", f"grade__{self.subject.id}__{self.student.id}__2026-05-15": "5"},
        )
        self.assertEqual(response.status_code, 302)
        grade.refresh_from_db()
        self.assertEqual(grade.value, "5")

    def test_admin_can_add_grade_by_form(self):
        self.client.login(username="admin_test", password="Pass12345!")
        response = self.client.post(
            f"{reverse('journal')}?group={self.group.id}",
            data={
                "action": "add_grade",
                "student": self.student.id,
                "subject": self.subject.id,
                "teacher": self.teacher.id,
                "date": "2026-05-16",
                "value": 4,
            },
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            Grade.objects.filter(
                student=self.student,
                subject=self.subject,
                teacher=self.teacher,
                date="2026-05-16",
                value="4",
            ).exists()
        )


class CourseApplicationFormTests(TestCase):
    def test_public_form_accepts_plus_seven_phone_format(self):
        form = CourseApplicationPublicForm(
            data={
                'last_name': 'Иванов',
                'first_name': 'Иван',
                'middle_name': 'Иванович',
                'gender': 'male',
                'birth_date': '2000-01-01',
                'city_church': 'Тамбов',
                'instrument': 'Баян I',
                'music_education': 'none',
                'student_phone': '+7 (999) 123-45-67',
                'parent_contacts': '',
                'comments': '',
            }
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['student_phone'], '+7 (999) 123-45-67')

    def test_public_form_does_not_show_parent_contacts_help_text(self):
        form = CourseApplicationPublicForm()

        self.assertEqual(form.fields['gender'].widget.__class__.__name__, 'RadioSelect')
        self.assertEqual(form.fields['parent_contacts'].help_text, '')


class AccountUtilityTests(TestCase):
    def test_build_username_from_full_name_uses_name_and_surname(self):
        self.assertEqual(build_username_from_full_name('Иван Иванов'), 'иван-иванов')

    def test_display_name_for_user_prefers_profile_full_name(self):
        user = User.objects.create_user(username='tempuser', password='Pass12345!', first_name='Иван', last_name='Иванов')
        student = Student.objects.create(full_name='Иван Иванов', group=Group.objects.create(name='Группа 1'), user=user)

        self.assertEqual(display_name_for_user(user), student.full_name)


class AccountCommandTests(TestCase):
    def test_create_student_accounts_uses_name_based_username_and_temp_password(self):
        group = Group.objects.create(name='Группа 2')
        student = Student.objects.create(full_name='Иван Иванов', group=group)

        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            call_command('create_student_accounts', stdout=StringIO())

        student.refresh_from_db()
        self.assertIsNotNone(student.user)
        self.assertEqual(student.user.username, 'иван-иванов')
        self.assertTrue(student.user.check_password('Temp12345!'))

    def test_create_student_accounts_adds_suffix_for_duplicate_names(self):
        group = Group.objects.create(name='Группа 3')
        first = Student.objects.create(full_name='Иван Иванов', group=group)
        second = Student.objects.create(full_name='Иван Иванов', group=group)

        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            call_command('create_student_accounts', stdout=StringIO())

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.user.username, 'иван-иванов')
        self.assertEqual(second.user.username, 'иван-иванов-2')
