from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

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
