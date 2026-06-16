from datetime import date, timedelta
from csv import writer
from pathlib import Path
from random import Random

from django.contrib.auth.models import User
from django.core.management.base import BaseCommand
from django.db import transaction

from journal.account_utils import build_display_name_from_full_name, build_username_from_full_name, generate_temporary_password, split_user_name
from journal.models import (
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    Group,
    Student,
    Subject,
    SubjectResult,
    Teacher,
    TemporaryCredential,
)


class Command(BaseCommand):
    help = 'Полностью заполняет БД тестовыми данными (группы, предметы, преподаватели, ученики, оценки, итоги, пользователи).'

    def add_arguments(self, parser):
        parser.add_argument(
            '--credentials-output',
            default='',
            help='Путь к CSV с тестовыми логинами/паролями. По умолчанию: secrets.csv в корне проекта.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        credentials = []

        CourseApplication.objects.all().delete()
        Grade.objects.all().delete()
        SubjectResult.objects.all().delete()
        Student.objects.all().delete()
        Teacher.objects.all().delete()
        Group.objects.all().delete()
        Subject.objects.all().delete()
        CourseRegistrationSettings.objects.all().delete()
        TemporaryCredential.objects.all().delete()
        User.objects.all().delete()

        CourseRegistrationSettings.objects.create(pk=1, telegram_group_url='')

        admin_password = generate_temporary_password()
        admin_user = User.objects.create_user(
            username='admin',
            email='admin@example.com',
            password=admin_password,
        )
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.save(update_fields=['is_staff', 'is_superuser'])
        credentials.append({
            'role': 'admin',
            'name': 'Администратор',
            'login': admin_user.username,
            'password': admin_password,
        })

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
        used_usernames = set(User.objects.values_list('username', flat=True))
        for full_name, subject_names in teacher_specs:
            password = generate_temporary_password()
            display_login = build_display_name_from_full_name(full_name)
            username = build_username_from_full_name(full_name, existing_usernames=used_usernames)
            user = User.objects.create_user(
                username=username,
                password=password,
                first_name=full_name.split()[0],
                last_name=' '.join(full_name.split()[1:]),
            )
            used_usernames.add(username)
            teacher = Teacher.objects.create(full_name=full_name, user=user)
            teacher.subjects.set([subjects[name] for name in subject_names])
            teachers[full_name] = teacher
            TemporaryCredential.objects.create(
                login=display_login,
                temporary_password=password,
            )
            credentials.append({
                'role': 'teacher',
                'name': full_name,
                'login': display_login,
                'password': password,
            })

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
        for group_name, full_names in student_map.items():
            for full_name in full_names:
                display_login = build_display_name_from_full_name(full_name)
                username = build_username_from_full_name(full_name, existing_usernames=used_usernames)
                temp_password = generate_temporary_password()
                first_name, last_name = split_user_name(full_name)
                user = User.objects.create_user(
                    username=username,
                    password=temp_password,
                    first_name=first_name,
                    last_name=last_name,
                )
                used_usernames.add(username)
                TemporaryCredential.objects.create(
                    login=display_login,
                    temporary_password=temp_password,
                )
                student = Student.objects.create(full_name=full_name, group=groups[group_name], user=user)
                students.append(student)
                by_name[full_name] = student
                credentials.append({
                    'role': 'student',
                    'name': full_name,
                    'login': display_login,
                    'password': temp_password,
                })

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

        output = options['credentials_output'].strip()
        if output:
            credentials_path = Path(output)
        else:
            credentials_path = Path.cwd() / 'secrets.csv'

        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        with credentials_path.open('w', encoding='utf-8', newline='') as stream:
            csv_writer = writer(stream)
            csv_writer.writerow(['role', 'name', 'login', 'password'])
            for row in credentials:
                csv_writer.writerow([
                    row['role'],
                    row['name'],
                    row['login'],
                    row['password'],
                ])

        self.stdout.write(self.style.SUCCESS('Тестовые данные успешно созданы.'))
        self.stdout.write(self.style.SUCCESS(f'Логины и пароли сохранены: {credentials_path}'))
        self.stdout.write(
            f'Пользователей: {User.objects.count()}, '
            f'групп: {Group.objects.count()}, '
            f'учеников: {Student.objects.count()}, '
            f'предметов: {Subject.objects.count()}, '
            f'преподавателей: {Teacher.objects.count()}, '
            f'оценок: {Grade.objects.count()}, '
            f'итогов: {SubjectResult.objects.count()}'
        )
