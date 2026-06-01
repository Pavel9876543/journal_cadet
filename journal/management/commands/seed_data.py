from datetime import date, timedelta
from random import Random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from journal.models import Grade, Group, Student, Subject, SubjectResult, Teacher


class Command(BaseCommand):
    help = 'Полностью заполняет БД тестовыми данными (группы, предметы, преподаватели, ученики, оценки, итоги, пользователи).'

    @transaction.atomic
    def handle(self, *args, **options):
        Grade.objects.all().delete()
        SubjectResult.objects.all().delete()
        Student.objects.all().delete()
        Teacher.objects.all().delete()
        Group.objects.all().delete()
        Subject.objects.all().delete()
        User.objects.filter(is_superuser=False).delete()

        admin_user, _ = User.objects.get_or_create(
            username='admin',
            defaults={
                'is_staff': True,
                'is_superuser': True,
                'email': 'admin@example.com',
            },
        )
        admin_user.set_password('admin12345')
        admin_user.save()

        subjects = {
            'Сольфеджио': Subject.objects.create(name='Сольфеджио', final_grade_type=Subject.FINAL_GRADE_TYPE_NUMERIC),
            'Фортепиано': Subject.objects.create(name='Фортепиано', final_grade_type=Subject.FINAL_GRADE_TYPE_NUMERIC),
            'Гитара': Subject.objects.create(name='Гитара', final_grade_type=Subject.FINAL_GRADE_TYPE_NUMERIC),
            'Вокал': Subject.objects.create(name='Вокал', final_grade_type=Subject.FINAL_GRADE_TYPE_NUMERIC),
            'Музыкальная литература': Subject.objects.create(name='Музыкальная литература', final_grade_type=Subject.FINAL_GRADE_TYPE_NUMERIC),
            'Гармония': Subject.objects.create(name='Гармония', final_grade_type=Subject.FINAL_GRADE_TYPE_NUMERIC),
            'Специальность по баяну': Subject.objects.create(
                name='Специальность по баяну',
                final_grade_type=Subject.FINAL_GRADE_TYPE_PASS_FAIL,
            ),
        }

        groups = {
            'Группа A (начинающие)': Group.objects.create(name='Группа A (начинающие)'),
            'Группа B (средний уровень)': Group.objects.create(name='Группа B (средний уровень)'),
            'Группа C (продвинутые)': Group.objects.create(name='Группа C (продвинутые)'),
        }

        groups['Группа A (начинающие)'].subjects.set([
            subjects['Сольфеджио'],
            subjects['Фортепиано'],
            subjects['Музыкальная литература'],
        ])
        groups['Группа B (средний уровень)'].subjects.set([
            subjects['Сольфеджио'],
            subjects['Гитара'],
            subjects['Вокал'],
            subjects['Музыкальная литература'],
        ])
        groups['Группа C (продвинутые)'].subjects.set([
            subjects['Гармония'],
            subjects['Фортепиано'],
            subjects['Вокал'],
            subjects['Музыкальная литература'],
        ])

        teacher_specs = [
            ('Анна Морозова', ['Сольфеджио', 'Музыкальная литература']),
            ('Дмитрий Ковалёв', ['Фортепиано', 'Гармония']),
            ('Елена Серова', ['Вокал', 'Сольфеджио']),
            ('Игорь Романов', ['Гитара', 'Музыкальная литература']),
            ('Марина Белова', ['Вокал', 'Фортепиано', 'Музыкальная литература']),
            ('Сергей Аксёнов', ['Специальность по баяну']),
        ]

        teachers = {}
        for idx, (full_name, subject_names) in enumerate(teacher_specs, start=1):
            user = User.objects.create_user(
                username=f'teacher{idx}',
                password='pass12345',
                first_name=full_name.split()[0],
                last_name=' '.join(full_name.split()[1:]),
            )
            teacher = Teacher.objects.create(full_name=full_name, user=user)
            teacher.subjects.set([subjects[name] for name in subject_names])
            teachers[full_name] = teacher

        student_map = {
            'Группа A (начинающие)': [
                'Артём Соколов',
                'Ксения Ильина',
                'Павел Громов',
                'София Фролова',
                'Михаил Титов',
            ],
            'Группа B (средний уровень)': [
                'Виктория Орлова',
                'Роман Карпов',
                'Алина Жукова',
                'Тимофей Фадеев',
                'Дарья Никитина',
            ],
            'Группа C (продвинутые)': [
                'Никита Мельников',
                'Полина Егорова',
                'Глеб Воронов',
                'Мария Ларионова',
                'Яна Тарасова',
            ],
        }

        students = []
        by_name = {}
        student_counter = 1
        for group_name, full_names in student_map.items():
            for full_name in full_names:
                user = User.objects.create_user(
                    username=f'student{student_counter}',
                    password='pass12345',
                    first_name=full_name.split()[0],
                    last_name=' '.join(full_name.split()[1:]),
                )
                student_counter += 1
                student = Student.objects.create(full_name=full_name, group=groups[group_name], user=user)
                students.append(student)
                by_name[full_name] = student

        bayan_subject = subjects['Специальность по баяну']
        bayan_students = [
            by_name['Ксения Ильина'],
            by_name['Дарья Никитина'],
            by_name['Никита Мельников'],
            by_name['Мария Ларионова'],
            by_name['Яна Тарасова'],
        ]
        bayan_subject.students.set(bayan_students)

        subject_teachers = {subject.id: list(subject.teachers.all()) for subject in Subject.objects.all()}
        rng = Random(2026)
        today = date.today()

        for student in students:
            regular_subjects = list(student.group.subjects.all())
            individual_subjects = list(student.individual_subjects.all())
            all_subjects = regular_subjects + [s for s in individual_subjects if s not in regular_subjects]

            for subject in all_subjects:
                candidates = subject_teachers[subject.id]
                teacher = candidates[rng.randrange(len(candidates))]

                if subject.final_grade_type == Subject.FINAL_GRADE_TYPE_PASS_FAIL:
                    grade_values = ['4', '5', '5']
                else:
                    grade_values = [str(rng.choice([3, 4, 4, 5, 5, 5])) for _ in range(3)]

                for i, grade_value in enumerate(grade_values):
                    Grade.objects.create(
                        student=student,
                        subject=subject,
                        teacher=teacher,
                        date=today - timedelta(days=(i * 7 + rng.randrange(0, 6))),
                        value=grade_value,
                    )

                if subject.final_grade_type == Subject.FINAL_GRADE_TYPE_PASS_FAIL:
                    final_value = rng.choice(['Зачет', 'Незачет', 'Зачет'])
                    exam_value = rng.choice(['Зачет', 'Незачет', 'Зачет'])
                else:
                    final_value = str(rng.choice([3, 4, 4, 5, 5]))
                    exam_value = str(rng.choice([3, 4, 4, 5, 5]))

                SubjectResult.objects.create(
                    student=student,
                    subject=subject,
                    exam_grade=exam_value,
                    final_grade=final_value,
                )

        self.stdout.write(self.style.SUCCESS('Тестовые данные успешно созданы.'))
        self.stdout.write(
            f'Пользователей: {User.objects.count()}, '
            f'групп: {Group.objects.count()}, '
            f'учеников: {Student.objects.count()}, '
            f'предметов: {Subject.objects.count()}, '
            f'преподавателей: {Teacher.objects.count()}, '
            f'оценок: {Grade.objects.count()}, '
            f'итогов: {SubjectResult.objects.count()}'
        )
        self.stdout.write('Логины: admin/admin12345, teacher1..teacher6/pass12345, student1..student15/pass12345')
