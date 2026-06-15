from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zipfile import ZipFile

from django.contrib.auth.models import User
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse

from journal.account_utils import build_course_application_login, build_display_name_from_full_name, build_username_from_full_name, display_name_for_user
from journal.forms import CourseApplicationPublicForm
from journal.models import CourseApplication, Grade, Group, Student, Subject, Teacher, TemporaryCredential, TemporaryStudentCredential


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
        self.assertContains(response, "Вы вошли как: Преподаватель Тестовый")
        self.assertContains(response, reverse("password_change"))
        self.assertNotContains(response, "Регистрация на курсы")

    def test_authenticated_user_can_open_password_change_page(self):
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.get(reverse("password_change"))
        self.assertEqual(response.status_code, 200)

    def test_login_form_uses_password_manager_autocomplete_fields(self):
        response = self.client.get(reverse("login"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="username"')
        self.assertContains(response, 'autocomplete="username"')
        self.assertContains(response, 'name="password"')
        self.assertContains(response, 'autocomplete="current-password"')

    def test_password_change_shows_incorrect_current_password_message(self):
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.post(
            reverse("password_change"),
            data={
                "old_password": "WrongPass123!",
                "new_password1": "NewPass123!",
                "new_password2": "NewPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Текущий пароль указан неверно.")
        self.assertNotContains(response, "Проверьте текущий пароль и новые значения.")
        self.assertContains(response, 'data-error-for="old_password"')
        self.assertContains(response, 'scrollIntoView')

    def test_password_change_shows_mismatch_message(self):
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.post(
            reverse("password_change"),
            data={
                "old_password": "Pass12345!",
                "new_password1": "NewPass123!",
                "new_password2": "OtherPass123!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Новый пароль и подтверждение не совпадают.")

    def test_password_change_rejects_unchanged_password(self):
        self.client.login(username="teacher_test", password="Pass12345!")
        response = self.client.post(
            reverse("password_change"),
            data={
                "old_password": "Pass12345!",
                "new_password1": "Pass12345!",
                "new_password2": "Pass12345!",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Новый пароль не должен совпадать со старым.")

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

    def test_grade_form_error_scrolls_to_invalid_field(self):
        self.client.login(username="admin_test", password="Pass12345!")
        response = self.client.post(
            f"{reverse('journal')}?group={self.group.id}&subject={self.subject.id}",
            data={
                "action": "add_grade",
                "student": self.student.id,
                "subject": self.subject.id,
                "teacher": self.teacher.id,
                "date": "",
                "value": 4,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'data-error-for="date"')
        self.assertContains(response, 'scrollIntoView')


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


class CourseRegistrationTemporaryCredentialTests(TestCase):
    def _registration_payload(self, **overrides):
        payload = {
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
        payload.update(overrides)
        return payload

    def test_course_application_save_creates_temporary_student_credential(self):
        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            application = CourseApplication.objects.create(**self._registration_payload())

        credential = TemporaryStudentCredential.objects.get()
        user = User.objects.get(username='Иванов Иван')
        student = Student.objects.get(user=user)
        self.assertEqual(application.student_phone, '+7 (999) 123-45-67')
        self.assertEqual(credential.login, 'Иванов Иван')
        self.assertEqual(credential.temporary_password, 'Temp12345!')
        self.assertEqual(credential.student_phone, '+7 (999) 123-45-67')
        self.assertTrue(user.check_password('Temp12345!'))
        self.assertEqual(student.full_name, 'Иван Иванов')
        self.assertEqual(student.group.name, CourseApplication.STUDENT_COURSE_GROUP_NAME)

    def test_course_application_save_adds_suffix_for_duplicate_login(self):
        payload = self._registration_payload()
        second_payload = self._registration_payload(student_phone='+7 (999) 123-45-68')

        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            CourseApplication.objects.create(**payload)
            CourseApplication.objects.create(**second_payload)

        self.assertEqual(
            list(TemporaryStudentCredential.objects.order_by('id').values_list('login', flat=True)),
            ['Иванов Иван', 'Иванов Иван 2'],
        )
        self.assertTrue(User.objects.filter(username='Иванов Иван 2').exists())

    def test_public_form_rejects_duplicate_student_phone(self):
        CourseApplication.objects.create(**self._registration_payload())

        form = CourseApplicationPublicForm(
            data=self._registration_payload(
                last_name='Петров',
                first_name='Пётр',
                middle_name='Петрович',
                student_phone='8 999 123 45 67',
            )
        )

        self.assertFalse(form.is_valid())
        self.assertIn('Ученик с таким номером телефона уже зарегистрирован.', form.errors['student_phone'])

    def test_registration_page_shows_generated_credentials(self):
        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            response = self.client.post(reverse('course_registration'), data=self._registration_payload())

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Иванов Иван')
        self.assertContains(response, 'Temp12345!')
        self.assertContains(response, 'Сохраните логин и временный пароль перед переходом в Telegram-группу.')
        self.assertContains(response, 'Скопировать данные')
        self.assertContains(response, 'Данные скопированы.')
        self.assertContains(response, 'Логин: ')
        self.assertContains(response, 'Пароль: ')
        self.assertNotContains(response, 'id="credential-login-form"')
        self.assertNotContains(response, 'name="username"')
        self.assertNotContains(response, 'name="password"')
        self.assertNotContains(response, 'PasswordCredential')
        self.assertNotContains(response, 'http-equiv="refresh"')
        self.assertNotContains(response, 'window.location.href')
        self.assertNotContains(response, 'Через несколько секунд')
        self.assertEqual(CourseApplication.objects.count(), 1)
        self.assertEqual(Student.objects.count(), 1)
        self.assertEqual(User.objects.count(), 1)

    def test_registered_student_can_login_with_generated_credentials(self):
        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            self.client.post(reverse('course_registration'), data=self._registration_payload())

        self.client.logout()
        logged_in = self.client.login(username='Иванов Иван', password='Temp12345!')

        self.assertTrue(logged_in)
        response = self.client.get(reverse('journal'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Иванов Иван')

    def test_registration_page_rejects_duplicate_phone_without_second_registration(self):
        CourseApplication.objects.create(**self._registration_payload())

        response = self.client.post(
            reverse('course_registration'),
            data=self._registration_payload(last_name='Петров', first_name='Пётр', student_phone='8 999 123 45 67'),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ученик с таким номером телефона уже зарегистрирован.')
        self.assertContains(response, 'data-error-for="student_phone"')
        self.assertContains(response, 'scrollToFirstServerError')
        self.assertEqual(CourseApplication.objects.count(), 1)
        self.assertEqual(TemporaryStudentCredential.objects.count(), 1)

    def test_registration_api_returns_generated_credentials(self):
        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            response = self.client.post(reverse('course_registration_api'), data=self._registration_payload())

        self.assertEqual(response.status_code, 201)
        payload = response.json()
        self.assertEqual(payload['login'], 'Иванов Иван')
        self.assertEqual(payload['temporary_password'], 'Temp12345!')

    def test_registration_api_rejects_duplicate_phone(self):
        CourseApplication.objects.create(**self._registration_payload())

        response = self.client.post(
            reverse('course_registration_api'),
            data=self._registration_payload(last_name='Петров', first_name='Пётр', student_phone='8 999 123 45 67'),
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn('student_phone', response.json()['errors'])
        self.assertEqual(CourseApplication.objects.count(), 1)


class AccountUtilityTests(TestCase):
    def test_build_username_from_full_name_uses_name_and_surname(self):
        self.assertEqual(build_display_name_from_full_name('Иван Иванов'), 'Иванов Иван')
        self.assertEqual(build_username_from_full_name('Иван Иванов'), 'иванов-иван')
        self.assertEqual(build_course_application_login('Иванов', 'Иван'), 'Иванов Иван')

    def test_display_name_for_user_prefers_profile_full_name(self):
        user = User.objects.create_user(username='tempuser', password='Pass12345!', first_name='Иван', last_name='Иванов')
        student = Student.objects.create(full_name='Иван Иванов', group=Group.objects.create(name='Группа 1'), user=user)

        self.assertEqual(display_name_for_user(user), 'Иванов Иван')


class AccountCommandTests(TestCase):
    def test_create_student_accounts_uses_name_based_username_and_temp_password(self):
        group = Group.objects.create(name='Группа 2')
        student = Student.objects.create(full_name='Иван Иванов', group=group)

        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            call_command('create_student_accounts', stdout=StringIO())

        student.refresh_from_db()
        self.assertIsNotNone(student.user)
        self.assertEqual(student.user.username, 'иванов-иван')
        self.assertTrue(student.user.check_password('Temp12345!'))
        credential = TemporaryCredential.objects.get(login='Иванов Иван')
        self.assertEqual(credential.temporary_password, 'Temp12345!')

    def test_create_student_accounts_adds_suffix_for_duplicate_names(self):
        group = Group.objects.create(name='Группа 3')
        first = Student.objects.create(full_name='Иван Иванов', group=group)
        second = Student.objects.create(full_name='Иван Иванов', group=group)

        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            call_command('create_student_accounts', stdout=StringIO())

        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.user.username, 'иванов-иван')
        self.assertEqual(second.user.username, 'иванов-иван-2')


class ExportTemporaryCredentialsTests(TestCase):
    def test_export_command_outputs_credentials_csv(self):
        group = Group.objects.create(name='Группа 6')
        Student.objects.create(full_name='Иван Иванов', group=group)

        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            call_command('create_student_accounts', stdout=StringIO())

        output = StringIO()
        call_command('export_temporary_credentials', stdout=output)

        csv_output = output.getvalue()
        self.assertIn('login,temporary_password,created_at', csv_output)
        self.assertIn('Иванов Иван', csv_output)
        self.assertIn('Temp12345!', csv_output)


class ExportStudentCredentialsWithPhoneTests(TestCase):
    def test_export_command_outputs_login_password_and_phone(self):
        with patch('journal.account_utils.generate_temporary_password', return_value='Temp12345!'):
            CourseApplication.objects.create(
                last_name='Петров',
                first_name='Пётр',
                middle_name='Петрович',
                gender='male',
                birth_date='2000-01-01',
                city_church='Тамбов',
                instrument='Баян I',
                music_education='none',
                student_phone='+7 (999) 123-45-67',
                parent_contacts='',
                comments='',
            )

        with TemporaryDirectory() as tmp_dir:
            output_path = Path(tmp_dir) / 'export.csv'
            call_command('export_student_credentials_with_phone', output=str(output_path))

            csv_output = output_path.read_text(encoding='utf-8')
            self.assertIn('login,temporary_password,student_phone', csv_output)
            self.assertIn('Петров Пётр', csv_output)
            self.assertIn('Temp12345!', csv_output)
            self.assertIn('+7 (999) 123-45-67', csv_output)

        self.assertEqual(TemporaryStudentCredential.objects.count(), 1)

class ExportStudentCredentialsXlsxTests(TestCase):
    def setUp(self):
        self.admin_user = User.objects.create_superuser(
            username='admin_xlsx',
            password='Pass12345!',
            email='admin-xlsx@example.com',
        )
        self.regular_user = User.objects.create_user(username='regular_xlsx', password='Pass12345!')
        TemporaryStudentCredential.objects.create(
            login='Иванов Иван',
            temporary_password='Temp12345!',
            student_phone='+7 (999) 123-45-67',
        )

    def test_superuser_can_download_xlsx(self):
        self.client.login(username='admin_xlsx', password='Pass12345!')
        response = self.client.get(reverse('export_student_credentials_xlsx'))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response['Content-Type'],
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        with ZipFile(BytesIO(response.content)) as archive:
            sheet = archive.read('xl/worksheets/sheet1.xml').decode('utf-8')

        self.assertIn('Логин', sheet)
        self.assertIn('Иванов Иван', sheet)
        self.assertIn('Temp12345!', sheet)
        self.assertIn('+7 (999) 123-45-67', sheet)

    def test_regular_user_cannot_download_xlsx(self):
        self.client.login(username='regular_xlsx', password='Pass12345!')
        response = self.client.get(reverse('export_student_credentials_xlsx'))

        self.assertEqual(response.status_code, 302)
