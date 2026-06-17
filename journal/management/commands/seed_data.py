from __future__ import annotations

from csv import writer
from datetime import date, timedelta
from pathlib import Path
from random import Random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from journal.account_utils import (
    build_username_from_full_name,
    generate_temporary_password,
    split_user_name,
)
from journal.models import (
    AcademicYear,
    CourseApplication,
    CourseRegistrationSettings,
    Grade,
    GroupSubject,
    Instrument,
    Student,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TeacherSubject,
    TemporaryCredential,
)


class Command(BaseCommand):
    help = (
        'Полностью заполняет БД тестовыми данными для электронного журнала музыкальной школы: '
        'пользователи, преподаватели, ученики, группы, предметы, назначения, оценки, итоги и заявки.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--credentials-output',
            default='',
            help='Путь к CSV с тестовыми логинами/паролями. По умолчанию: secrets.csv в корне проекта.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        self.credentials: list[dict[str, str]] = []
        self.used_usernames: set[str] = set()
        self.UserModel = get_user_model()

        self._clear_database()
        self.used_usernames = set(self.UserModel.objects.values_list('username', flat=True))

        CourseRegistrationSettings.objects.create(pk=1, telegram_group_url='')

        academic_year = self._create_current_academic_year()
        instruments = self._create_instruments()
        subjects = self._create_subjects()
        groups = self._create_groups(academic_year)

        self._create_admin_user()
        teachers = self._create_teachers(subjects)
        self._create_group_subjects(groups, subjects, teachers)
        students = self._create_students(groups, instruments, subjects, teachers)
        self._create_grades_and_results(students, academic_year)
        self._create_course_applications()

        credentials_path = self._write_credentials(options['credentials_output'])

        self.stdout.write(self.style.SUCCESS('Тестовые данные успешно созданы.'))
        self.stdout.write(self.style.SUCCESS(f'Логины и пароли сохранены: {credentials_path}'))
        self.stdout.write(
            f'Пользователей: {self.UserModel.objects.count()}, '
            f'учебных годов: {AcademicYear.objects.count()}, '
            f'групп: {StudyGroup.objects.count()}, '
            f'учеников: {Student.objects.count()}, '
            f'предметов: {Subject.objects.count()}, '
            f'преподавателей: {Teacher.objects.count()}, '
            f'предметов групп: {GroupSubject.objects.count()}, '
            f'индивидуальных предметов: {StudentSubject.objects.count()}, '
            f'оценок: {Grade.objects.count()}, '
            f'итогов: {SubjectResult.objects.count()}, '
            f'заявок: {CourseApplication.objects.count()}, '
            f'временных учетных данных: {TemporaryCredential.objects.count()}'
        )

    def _clear_database(self) -> None:
        """
        Очищает учебные данные в порядке, безопасном для PROTECT-связей.
        QuerySet.delete() не вызывает кастомный CourseApplication.delete(), поэтому удаляем всё явно.
        """
        TemporaryCredential.objects.all().delete()
        Grade.objects.all().delete()
        SubjectResult.objects.all().delete()
        StudentSubject.objects.all().delete()
        GroupSubject.objects.all().delete()
        TeacherSubject.objects.all().delete()
        CourseApplication.objects.all().delete()
        Student.objects.all().delete()
        Teacher.objects.all().delete()
        StudyGroup.objects.all().delete()
        Subject.objects.all().delete()
        Instrument.objects.all().delete()
        AcademicYear.objects.all().delete()
        CourseRegistrationSettings.objects.all().delete()
        self.UserModel.objects.all().delete()

    def _create_current_academic_year(self) -> AcademicYear:
        today = timezone.localdate()
        start_year = today.year if today.month >= 9 else today.year - 1

        return AcademicYear.objects.create(
            name=f'{start_year}/{start_year + 1}',
            starts_on=date(start_year, 9, 1),
            ends_on=date(start_year + 1, 8, 31),
            is_active=True,
        )

    def _create_instruments(self) -> dict[str, Instrument]:
        instrument_names = [
            'Баян',
            'Фортепиано',
            'Гитара',
            'Вокал',
            'Скрипка',
            'Домра',
            'Балалайка',
            'Флейта',
            CourseApplication.DEFAULT_INSTRUMENT_NAME,
        ]
        return {
            name: Instrument.objects.create(name=name)
            for name in instrument_names
        }

    def _create_subjects(self) -> dict[str, Subject]:
        subject_specs = [
            ('Сольфеджио', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Музыкальная литература', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Слушание музыки', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Хор', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Ансамбль', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Фортепиано', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Гитара', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Вокал', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Гармония', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Специальность', Subject.FINAL_GRADE_TYPE_NUMERIC, True),
        ]

        return {
            name: Subject.objects.create(
                name=name,
                final_grade_type=final_grade_type,
                is_specialty=is_specialty,
                is_active=True,
            )
            for name, final_grade_type, is_specialty in subject_specs
        }

    def _create_groups(self, academic_year: AcademicYear) -> dict[str, StudyGroup]:
        group_names = [
            '1 класс (начинающие)',
            '2 класс (средний уровень)',
            '3 класс (продвинутые)',
        ]
        return {
            name: StudyGroup.objects.create(
                name=name,
                academic_year=academic_year,
                is_active=True,
            )
            for name in group_names
        }

    def _create_admin_user(self) -> None:
        password = generate_temporary_password()
        admin_user = self.UserModel.objects.create_user(
            username='admin',
            email='admin@example.com',
            password=password,
        )
        admin_user.is_staff = True
        admin_user.is_superuser = True
        admin_user.save(update_fields=['is_staff', 'is_superuser'])

        self.used_usernames.add(admin_user.username)
        self._add_credentials('admin', 'Администратор', admin_user.username, password)

    def _create_teachers(self, subjects: dict[str, Subject]) -> dict[str, Teacher]:
        teacher_specs = [
            ('Анна Морозова', ['Сольфеджио', 'Музыкальная литература', 'Слушание музыки']),
            ('Дмитрий Ковалёв', ['Фортепиано', 'Гармония', 'Ансамбль', 'Специальность']),
            ('Елена Серова', ['Вокал', 'Сольфеджио', 'Хор', 'Специальность']),
            ('Игорь Романов', ['Гитара', 'Музыкальная литература', 'Ансамбль', 'Специальность']),
            ('Марина Белова', ['Фортепиано', 'Вокал', 'Хор', 'Музыкальная литература', 'Специальность']),
            ('Сергей Аксёнов', ['Специальность', 'Ансамбль', 'Сольфеджио']),
        ]

        teachers: dict[str, Teacher] = {}
        for full_name, subject_names in teacher_specs:
            user, password = self._create_user_for_full_name(full_name)
            teacher = Teacher.objects.create(
                full_name=full_name,
                user=user,
                is_active=True,
            )

            for subject_name in subject_names:
                TeacherSubject.objects.create(
                    teacher=teacher,
                    subject=subjects[subject_name],
                )

            TemporaryCredential.objects.create(
                login=user.username,
                temporary_password=password,
            )
            self._add_credentials('teacher', full_name, user.username, password)
            teachers[full_name] = teacher

        return teachers

    def _create_group_subjects(
        self,
        groups: dict[str, StudyGroup],
        subjects: dict[str, Subject],
        teachers: dict[str, Teacher],
    ) -> None:
        assignment_specs = [
            ('1 класс (начинающие)', 'Сольфеджио', 'Анна Морозова', 10),
            ('1 класс (начинающие)', 'Слушание музыки', 'Анна Морозова', 20),
            ('1 класс (начинающие)', 'Хор', 'Марина Белова', 30),
            ('1 класс (начинающие)', 'Фортепиано', 'Дмитрий Ковалёв', 40),

            ('2 класс (средний уровень)', 'Сольфеджио', 'Елена Серова', 10),
            ('2 класс (средний уровень)', 'Музыкальная литература', 'Игорь Романов', 20),
            ('2 класс (средний уровень)', 'Хор', 'Марина Белова', 30),
            ('2 класс (средний уровень)', 'Ансамбль', 'Игорь Романов', 40),

            ('3 класс (продвинутые)', 'Сольфеджио', 'Сергей Аксёнов', 10),
            ('3 класс (продвинутые)', 'Гармония', 'Дмитрий Ковалёв', 20),
            ('3 класс (продвинутые)', 'Музыкальная литература', 'Марина Белова', 30),
            ('3 класс (продвинутые)', 'Ансамбль', 'Сергей Аксёнов', 40),
        ]

        for group_name, subject_name, teacher_name, sort_order in assignment_specs:
            GroupSubject.objects.create(
                group=groups[group_name],
                subject=subjects[subject_name],
                teacher=teachers[teacher_name],
                sort_order=sort_order,
                is_active=True,
            )

    def _create_students(
        self,
        groups: dict[str, StudyGroup],
        instruments: dict[str, Instrument],
        subjects: dict[str, Subject],
        teachers: dict[str, Teacher],
    ) -> list[Student]:
        student_specs = [
            ('Артём Соколов', '1 класс (начинающие)', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Ксения Ильина', '1 класс (начинающие)', 'Баян', 'Сергей Аксёнов'),
            ('Павел Громов', '1 класс (начинающие)', 'Гитара', 'Игорь Романов'),
            ('София Фролова', '1 класс (начинающие)', 'Вокал', 'Елена Серова'),
            ('Михаил Титов', '1 класс (начинающие)', 'Фортепиано', 'Марина Белова'),

            ('Виктория Орлова', '2 класс (средний уровень)', 'Вокал', 'Елена Серова'),
            ('Роман Карпов', '2 класс (средний уровень)', 'Гитара', 'Игорь Романов'),
            ('Алина Жукова', '2 класс (средний уровень)', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Тимофей Фадеев', '2 класс (средний уровень)', 'Баян', 'Сергей Аксёнов'),
            ('Дарья Никитина', '2 класс (средний уровень)', 'Гитара', 'Игорь Романов'),

            ('Никита Мельников', '3 класс (продвинутые)', 'Баян', 'Сергей Аксёнов'),
            ('Полина Егорова', '3 класс (продвинутые)', 'Флейта', 'Марина Белова'),
            ('Глеб Воронов', '3 класс (продвинутые)', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Мария Ларионова', '3 класс (продвинутые)', 'Вокал', 'Елена Серова'),
            ('Яна Тарасова', '3 класс (продвинутые)', 'Домра', 'Сергей Аксёнов'),
        ]

        students: list[Student] = []
        specialty_subject = subjects['Специальность']

        for full_name, group_name, instrument_name, specialty_teacher_name in student_specs:
            user, password = self._create_user_for_full_name(full_name)
            student = Student.objects.create(
                full_name=full_name,
                group=groups[group_name],
                instrument=instruments[instrument_name],
                user=user,
                is_active=True,
            )
            StudentSubject.objects.create(
                student=student,
                subject=specialty_subject,
                teacher=teachers[specialty_teacher_name],
                is_specialty=True,
                is_active=True,
            )
            TemporaryCredential.objects.create(
                login=user.username,
                temporary_password=password,
            )
            self._add_credentials('student', full_name, user.username, password)
            students.append(student)

        return students

    def _create_grades_and_results(self, students: list[Student], academic_year: AcademicYear) -> None:
        rng = Random(2026)
        today = timezone.localdate()
        first_grade_date = max(academic_year.starts_on + timedelta(days=14), today - timedelta(days=45))

        for student in students:
            group_assignments = list(
                GroupSubject.objects
                .select_related('subject', 'teacher')
                .filter(group=student.group, is_active=True)
                .order_by('sort_order', 'subject__name')
            )
            individual_assignments = list(
                StudentSubject.objects
                .select_related('subject', 'teacher')
                .filter(student=student, is_active=True)
                .order_by('subject__name')
            )

            assignment_items = [
                (assignment.subject, assignment.teacher)
                for assignment in group_assignments
            ]
            assignment_items.extend(
                (assignment.subject, assignment.teacher)
                for assignment in individual_assignments
                if assignment.subject_id not in {subject.id for subject, _teacher in assignment_items}
            )

            for subject, teacher in assignment_items:
                grade_values = [str(rng.choice([3, 4, 4, 5, 5, 5])) for _ in range(3)]
                for index, grade_value in enumerate(grade_values):
                    grade_date = first_grade_date + timedelta(days=index * 14 + rng.randrange(0, 5))
                    if grade_date > today:
                        grade_date = today - timedelta(days=index)
                    Grade.objects.create(
                        student=student,
                        subject=subject,
                        teacher=teacher,
                        academic_year=academic_year,
                        date=grade_date,
                        value=grade_value,
                    )

                if subject.final_grade_type == Subject.FINAL_GRADE_TYPE_PASS_FAIL:
                    final_value = rng.choice(['Зачет', 'Зачет', 'Незачет'])
                    exam_value = rng.choice(['Зачет', 'Зачет', 'Незачет'])
                else:
                    final_value = str(rng.choice([3, 4, 4, 5, 5]))
                    exam_value = str(rng.choice([3, 4, 4, 5, 5]))

                SubjectResult.objects.create(
                    student=student,
                    subject=subject,
                    academic_year=academic_year,
                    exam_grade=exam_value,
                    final_grade=final_value,
                )

    def _create_course_applications(self) -> None:
        application_specs = [
            {
                'last_name': 'Смирнова',
                'first_name': 'Елизавета',
                'middle_name': 'Олеговна',
                'gender': CourseApplication.GENDER_FEMALE,
                'birth_date': date(2014, 3, 12),
                'city_church': 'Москва / Центральная церковь',
                'instrument': 'Скрипка',
                'music_education': CourseApplication.MUSIC_EDUCATION_BASIC,
                'student_phone': '+7 900 111-22-33',
                'parent_contacts': 'Ольга Смирнова - +7 900 111-22-34',
                'comments': 'Хочет заниматься по субботам.',
                'status': CourseApplication.STATUS_CONFIRMED,
            },
            {
                'last_name': 'Кузнецов',
                'first_name': 'Матвей',
                'middle_name': 'Игоревич',
                'gender': CourseApplication.GENDER_MALE,
                'birth_date': date(2013, 11, 5),
                'city_church': 'Тверь / Молодежная группа',
                'instrument': 'Баян',
                'music_education': CourseApplication.MUSIC_EDUCATION_NONE,
                'student_phone': '+7 901 222-33-44',
                'parent_contacts': 'Ирина Кузнецова - +7 901 222-33-45',
                'comments': 'Нужен начальный уровень.',
                'status': CourseApplication.STATUS_CONFIRMED,
            },
            {
                'last_name': 'Петрова',
                'first_name': 'Анастасия',
                'middle_name': 'Сергеевна',
                'gender': CourseApplication.GENDER_FEMALE,
                'birth_date': date(2015, 6, 20),
                'city_church': 'Коломна / Дом молитвы',
                'instrument': 'Вокал',
                'music_education': CourseApplication.MUSIC_EDUCATION_SELF,
                'student_phone': '+7 902 333-44-55',
                'parent_contacts': 'Сергей Петров - +7 902 333-44-56',
                'comments': 'Заявка отклонена для проверки логики удаления ученика из журнала.',
                'status': CourseApplication.STATUS_REJECTED,
            },
        ]

        for application_data in application_specs:
            application = CourseApplication.objects.create(**application_data)
            application.refresh_from_db()

            credential = getattr(application, 'temporary_credential', None)
            if credential is not None:
                self._add_credentials(
                    'course_student',
                    application.full_name,
                    credential.login,
                    credential.temporary_password,
                )

    def _create_user_for_full_name(self, full_name: str):
        username = build_username_from_full_name(
            full_name,
            existing_usernames=self.used_usernames,
        )
        password = generate_temporary_password()
        first_name, last_name = split_user_name(full_name)

        user = self.UserModel.objects.create_user(
            username=username,
            password=password,
            first_name=first_name,
            last_name=last_name,
        )
        self.used_usernames.add(username)
        return user, password

    def _add_credentials(self, role: str, name: str, login: str, password: str) -> None:
        self.credentials.append({
            'role': role,
            'name': name,
            'login': login,
            'password': password,
        })

    def _write_credentials(self, output: str) -> Path:
        output = (output or '').strip()
        if output:
            credentials_path = Path(output)
        else:
            credentials_path = Path.cwd() / 'secrets.csv'

        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        with credentials_path.open('w', encoding='utf-8', newline='') as stream:
            csv_writer = writer(stream)
            csv_writer.writerow(['role', 'name', 'login', 'password'])
            for row in self.credentials:
                csv_writer.writerow([
                    row['role'],
                    row['name'],
                    row['login'],
                    row['password'],
                ])

        return credentials_path
