from __future__ import annotations

import os
from csv import writer
from datetime import date, timedelta
from pathlib import Path
from random import Random

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.db import transaction

from journal.account_utils import (
    build_username_from_full_name,
    display_name_for_user,
    ensure_temporary_credential_for_user,
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


class Command(BaseCommand):

    ADMIN_GROUP_NAME = 'Администратор'
    TEACHER_GROUP_NAME = 'Преподаватель'
    STUDENT_GROUP_NAME = 'Ученик'

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

        self.role_groups = self._create_role_groups()
        self._assign_role_to_existing_admins()

        CourseRegistrationSettings.objects.update_or_create(
            pk=1,
            defaults={
                'telegram_group_url': 'https://t.me/cadet_journal_demo',
                'minimum_registration_age': 14,
                'course_starts_on': date(2025, 9, 1),
                'course_ends_on': date(2026, 8, 31),
            },
        )
        for contact_data in (
            {
                'name': 'Дежурный администратор',
                'phone': '+7 (900) 000-00-01',
                'messengers': 'Telegram, WhatsApp',
                'display_order': 10,
            },
            {
                'name': 'Учебная часть',
                'phone': '+7 (900) 000-00-02',
                'messengers': 'Telegram',
                'display_order': 20,
            },
        ):
            PasswordRecoveryContact.objects.create(**contact_data)

        academic_year = self._create_current_academic_year()
        instruments = self._create_instruments()
        subjects = self._create_subjects()
        groups = self._create_groups(academic_year)

        teachers = self._create_teachers(subjects)
        self._create_group_subjects(groups, subjects, teachers)
        self._create_students(groups, instruments, subjects, teachers)
        self._create_course_applications()
        self._create_course_group_assignments(academic_year, subjects, teachers)
        self._ensure_temporary_credentials_for_all_users()

        students = list(
            Student.objects
            .filter(is_active=True)
            .select_related('group', 'instrument')
            .order_by('id')
        )
        self._create_grades_and_results(students, academic_year)

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

    def _clear_database(self):
        """
        Очищает только тестовые данные журнала.

        Важно:
        - суперпользователи не удаляются;
        - staff-пользователи не удаляются;
        - пользователь из DJANGO_SUPERUSER_USERNAME не удаляется;
        - вручную созданные админы сохраняются;
        - временные учетные данные учеников удаляются;
        - тестовые ученики и преподаватели удаляются вместе с их User-аккаунтами,
          но только если эти аккаунты не являются staff/superuser.
        """
        User = get_user_model()

        admin_username = os.getenv('DJANGO_SUPERUSER_USERNAME')

        protected_users = User.objects.filter(
            is_superuser=True,
        )

        protected_user_ids = set(
            protected_users.values_list('id', flat=True),
        )

        protected_user_ids.update(
            User.objects.filter(is_staff=True).values_list('id', flat=True),
        )

        if admin_username:
            protected_user_ids.update(
                User.objects.filter(username=admin_username).values_list('id', flat=True),
            )

        # Сначала удаляем зависимые учебные данные.
        TemporaryCredential.objects.all().delete()
        PasswordRecoveryContact.objects.all().delete()
        CourseApplication.objects.all().delete()
        SubjectResult.objects.all().delete()
        Grade.objects.all().delete()
        StudentSubject.objects.all().delete()
        GroupSubject.objects.all().delete()
        TeacherSubject.objects.all().delete()

        # Запоминаем пользователей учеников и преподавателей,
        # чтобы удалить только неадминские аккаунты.
        student_user_ids = set(
            Student.objects.exclude(user_id__in=protected_user_ids)
            .exclude(user__isnull=True)
            .values_list('user_id', flat=True)
        )

        teacher_user_ids = set(
            Teacher.objects.exclude(user_id__in=protected_user_ids)
            .exclude(user__isnull=True)
            .values_list('user_id', flat=True)
        )

        users_to_delete_ids = student_user_ids | teacher_user_ids

        # Удаляем учебные профили.
        Student.objects.all().delete()
        Teacher.objects.all().delete()

        # Удаляем только обычных пользователей, созданных для тестовых учеников/преподавателей.
        User.objects.filter(id__in=users_to_delete_ids).exclude(
            id__in=protected_user_ids,
        ).delete()

        # Очищаем справочники.
        StudyGroup.objects.all().delete()
        Subject.objects.all().delete()
        Instrument.objects.all().delete()
        AcademicYear.objects.all().delete()

    def _create_role_groups(self) -> dict[str, Group]:
        """
        Создает группы ролей для пользователей.

        Эти группы видны в стандартной админке Django:
        Пользователи -> конкретный пользователь -> Группы.
        """
        group_names = [
            self.ADMIN_GROUP_NAME,
            self.TEACHER_GROUP_NAME,
            self.STUDENT_GROUP_NAME,
        ]

        return {
            group_name: Group.objects.get_or_create(name=group_name)[0]
            for group_name in group_names
        }

    def _assign_role_to_existing_admins(self) -> None:
        """
        Назначает роль администратора уже существующим админам.

        Важно:
        - пароль не меняется;
        - пользователь не пересоздается;
        - вручную созданный админ сохраняется;
        - админ из GitHub Secrets сохраняется.
        """
        admin_group = self.role_groups[self.ADMIN_GROUP_NAME]

        admin_username = os.getenv('DJANGO_SUPERUSER_USERNAME')

        admin_users = self.UserModel.objects.filter(
            is_staff=True,
            is_superuser=True,
        )

        if admin_username:
            admin_users = admin_users | self.UserModel.objects.filter(
                username=admin_username,
            )

        for user in admin_users.distinct():
            user.groups.add(admin_group)

    def _ensure_temporary_credentials_for_all_users(self) -> None:
        exported_logins = {row['login'] for row in self.credentials}
        users = (
            self.UserModel.objects
            .filter(is_active=True)
            .select_related('student_profile', 'teacher_profile')
            .prefetch_related('groups')
            .order_by('id')
        )

        for user in users:
            credential = ensure_temporary_credential_for_user(
                user,
                reset_missing_password=True,
            )
            if user.username in exported_logins:
                continue
            self._add_credentials(
                self._credential_role_for_user(user),
                display_name_for_user(user) or user.username,
                credential.login,
                credential.temporary_password,
            )
            exported_logins.add(user.username)

    def _credential_role_for_user(self, user) -> str:
        group_names = set(user.groups.values_list('name', flat=True))
        if self.ADMIN_GROUP_NAME in group_names or user.is_superuser or user.is_staff:
            return 'admin'
        if self.TEACHER_GROUP_NAME in group_names:
            return 'teacher'
        if self.STUDENT_GROUP_NAME in group_names:
            return 'student'
        return 'user'

    def _create_current_academic_year(self) -> AcademicYear:
        return AcademicYear.objects.create(
            name='2025/2026',
            starts_on=date(2025, 9, 1),
            ends_on=date(2026, 8, 31),
            is_active=True,
        )

    def _create_instruments(self) -> dict[str, Instrument]:
        instrument_names = [
            'Аккордеон',
            'Баян',
            'Балалайка',
            'Виолончель',
            'Домра',
            'Кларнет',
            'Гитара',
            'Саксофон',
            'Скрипка',
            'Флейта',
            'Фортепиано',
            'Ударные',
            'Хоровая партия',
            'Вокал',
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
            ('Ритмика', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Хор', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Ансамбль', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Оркестр', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False),
            ('Фортепиано', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Гитара', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Вокал', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Гармония', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Дирижирование', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Импровизация', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('История церковной музыки', Subject.FINAL_GRADE_TYPE_NUMERIC, False),
            ('Специальность', Subject.FINAL_GRADE_TYPE_NUMERIC, True),
            ('Индивидуальная импровизация', Subject.FINAL_GRADE_TYPE_NUMERIC, True),
            ('Индивидуальное дирижирование', Subject.FINAL_GRADE_TYPE_NUMERIC, True),
            ('Индивидуальный оркестр', Subject.FINAL_GRADE_TYPE_PASS_FAIL, True),
            ('Индивидуальная история церковной музыки', Subject.FINAL_GRADE_TYPE_NUMERIC, True),
            ('Индивидуальный ансамбль', Subject.FINAL_GRADE_TYPE_PASS_FAIL, True),
            ('Индивидуальная гитара', Subject.FINAL_GRADE_TYPE_NUMERIC, True),
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
        group_specs = [
            ('Подготовительная группа', True),
            ('1 класс (начинающие)', True),
            ('2 класс (средний уровень)', True),
            ('3 класс (продвинутые)', True),
            ('Старший ансамбль', True),
            ('Архивная группа', False),
        ]
        return {
            name: StudyGroup.objects.create(
                name=name,
                academic_year=academic_year,
                is_active=is_active,
            )
            for name, is_active in group_specs
        }

    def _create_teachers(self, subjects: dict[str, Subject]) -> dict[str, Teacher]:
        teacher_specs = [
            {
                'full_name': 'Анна Морозова',
                'birth_date': date(1981, 2, 21),
                'phone': '+7 (900) 100-00-01',
                'email': 'anna.morozova@cadet-journal.local',
                'comments': (
                    'Куратор теоретического блока. Ведет сольфеджио, слушание музыки '
                    'и вводные занятия для подготовительной группы.'
                ),
                'subjects': ['Сольфеджио', 'Музыкальная литература', 'Слушание музыки', 'Ритмика'],
            },
            {
                'full_name': 'Дмитрий Ковалёв',
                'birth_date': date(1979, 4, 12),
                'phone': '+7 (900) 100-00-02',
                'email': 'dmitry.kovalev@cadet-journal.local',
                'comments': (
                    'Педагог по фортепиано и гармонии. Отвечает за подготовку '
                    'к итоговым прослушиваниям и аккомпанемент.'
                ),
                'subjects': ['Фортепиано', 'Гармония', 'Ансамбль', 'Импровизация', 'Специальность'],
            },
            {
                'full_name': 'Елена Серова',
                'birth_date': date(1985, 7, 8),
                'phone': '+7 (900) 100-00-03',
                'email': 'elena.serova@cadet-journal.local',
                'comments': (
                    'Вокальный педагог и руководитель младшего хора. Следит за '
                    'дыханием, дикцией и сценической уверенностью учеников.'
                ),
                'subjects': ['Вокал', 'Сольфеджио', 'Хор', 'Специальность'],
            },
            {
                'full_name': 'Игорь Романов',
                'birth_date': date(1982, 9, 17),
                'phone': '+7 (900) 100-00-04',
                'email': 'igor.romanov@cadet-journal.local',
                'comments': (
                    'Преподаватель гитары, ансамбля и оркестровой практики. '
                    'Ведет репетиции смешанных составов.'
                ),
                'subjects': ['Гитара', 'Музыкальная литература', 'Ансамбль', 'Оркестр', 'Специальность'],
            },
            {
                'full_name': 'Марина Белова',
                'birth_date': date(1976, 12, 3),
                'phone': '+7 (900) 100-00-05',
                'email': 'marina.belova@cadet-journal.local',
                'comments': (
                    'Старший преподаватель. Курирует экзамены, вокальные ансамбли '
                    'и индивидуальные консультации по фортепиано.'
                ),
                'subjects': ['Фортепиано', 'Вокал', 'Хор', 'Музыкальная литература', 'Специальность'],
            },
            {
                'full_name': 'Сергей Аксёнов',
                'birth_date': date(1974, 5, 25),
                'phone': '+7 (900) 100-00-06',
                'email': 'sergey.aksyonov@cadet-journal.local',
                'comments': (
                    'Преподаватель народных инструментов. Ведет специальность, '
                    'ансамбль и консультации для старших учеников.'
                ),
                'subjects': ['Специальность', 'Ансамбль', 'Сольфеджио', 'Импровизация'],
            },
            {
                'full_name': 'Наталья Лебедева',
                'birth_date': date(1988, 1, 30),
                'phone': '+7 (900) 100-00-07',
                'email': 'natalia.lebedeva@cadet-journal.local',
                'comments': (
                    'Ведет струнные инструменты, оркестр и камерные составы. '
                    'Помогает ученикам готовить партии к общим служениям.'
                ),
                'subjects': ['Оркестр', 'Ансамбль', 'Слушание музыки', 'Специальность'],
            },
            {
                'full_name': 'Алексей Ветров',
                'birth_date': date(1983, 10, 14),
                'phone': '+7 (900) 100-00-08',
                'email': 'alexey.vetrov@cadet-journal.local',
                'comments': (
                    'Преподаватель духовых инструментов и ритмики. Отвечает за '
                    'ансамблевую дисциплину и работу с метрономом.'
                ),
                'subjects': ['Ритмика', 'Оркестр', 'Ансамбль', 'Специальность'],
            },
            {
                'full_name': 'Ольга Захарова',
                'birth_date': date(1977, 8, 6),
                'phone': '+7 (900) 100-00-09',
                'email': 'olga.zakharova@cadet-journal.local',
                'comments': (
                    'Преподаватель дирижирования и истории церковной музыки. '
                    'Проводит зачеты по хоровому служению.'
                ),
                'subjects': ['Дирижирование', 'История церковной музыки', 'Хор', 'Сольфеджио'],
            },
        ]

        teachers: dict[str, Teacher] = {}
        for teacher_data in teacher_specs:
            full_name = teacher_data['full_name']
            user, password = self._create_user_for_full_name(
                full_name,
                email=teacher_data['email'],
            )
            self._assign_user_role(user, self.TEACHER_GROUP_NAME)

            teacher = Teacher.objects.create(
                full_name=full_name,
                birth_date=teacher_data['birth_date'],
                phone=teacher_data['phone'],
                email=teacher_data['email'],
                comments=teacher_data['comments'],
                user=user,
                is_active=True,
            )

            for subject_name in teacher_data['subjects']:
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
            ('Подготовительная группа', 'Ритмика', 'Алексей Ветров', 10, True),
            ('Подготовительная группа', 'Слушание музыки', 'Анна Морозова', 20, True),
            ('Подготовительная группа', 'Хор', 'Елена Серова', 30, True),
            ('Подготовительная группа', 'Фортепиано', 'Дмитрий Ковалёв', 40, True),

            ('1 класс (начинающие)', 'Ритмика', 'Алексей Ветров', 10, True),
            ('1 класс (начинающие)', 'Сольфеджио', 'Анна Морозова', 20, True),
            ('1 класс (начинающие)', 'Слушание музыки', 'Анна Морозова', 30, True),
            ('1 класс (начинающие)', 'Хор', 'Марина Белова', 40, True),
            ('1 класс (начинающие)', 'Фортепиано', 'Дмитрий Ковалёв', 50, True),

            ('2 класс (средний уровень)', 'Сольфеджио', 'Елена Серова', 10, True),
            ('2 класс (средний уровень)', 'Музыкальная литература', 'Игорь Романов', 20, True),
            ('2 класс (средний уровень)', 'Хор', 'Марина Белова', 30, True),
            ('2 класс (средний уровень)', 'Ансамбль', 'Игорь Романов', 40, True),
            ('2 класс (средний уровень)', 'Гитара', 'Игорь Романов', 50, True),
            ('2 класс (средний уровень)', 'Импровизация', 'Сергей Аксёнов', 60, True),

            ('3 класс (продвинутые)', 'Сольфеджио', 'Сергей Аксёнов', 10, True),
            ('3 класс (продвинутые)', 'Гармония', 'Дмитрий Ковалёв', 20, True),
            ('3 класс (продвинутые)', 'Музыкальная литература', 'Марина Белова', 30, True),
            ('3 класс (продвинутые)', 'Ансамбль', 'Сергей Аксёнов', 40, True),
            ('3 класс (продвинутые)', 'Оркестр', 'Наталья Лебедева', 50, True),
            ('3 класс (продвинутые)', 'Дирижирование', 'Ольга Захарова', 60, True),

            ('Старший ансамбль', 'Сольфеджио', 'Ольга Захарова', 10, True),
            ('Старший ансамбль', 'Гармония', 'Дмитрий Ковалёв', 20, True),
            ('Старший ансамбль', 'История церковной музыки', 'Ольга Захарова', 30, True),
            ('Старший ансамбль', 'Оркестр', 'Игорь Романов', 40, True),
            ('Старший ансамбль', 'Ансамбль', 'Наталья Лебедева', 50, True),
            ('Старший ансамбль', 'Дирижирование', 'Ольга Захарова', 60, True),

            ('Архивная группа', 'Сольфеджио', 'Анна Морозова', 10, False),
        ]

        for assignment_spec in assignment_specs:
            if len(assignment_spec) == 4:
                group_name, subject_name, teacher_name, sort_order = assignment_spec
                is_active = True
            else:
                group_name, subject_name, teacher_name, sort_order, is_active = assignment_spec
            GroupSubject.objects.create(
                group=groups[group_name],
                subject=subjects[subject_name],
                teacher=teachers[teacher_name],
                sort_order=sort_order,
                is_active=is_active,
            )

    def _create_students(
        self,
        groups: dict[str, StudyGroup],
        instruments: dict[str, Instrument],
        subjects: dict[str, Subject],
        teachers: dict[str, Teacher],
    ) -> list[Student]:
        student_specs = [
            ('Лев Андреев', Student.GENDER_MALE, 'Подготовительная группа', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Ева Богданова', Student.GENDER_FEMALE, 'Подготовительная группа', 'Вокал', 'Елена Серова'),
            ('Матвей Денисов', Student.GENDER_MALE, 'Подготовительная группа', 'Баян', 'Сергей Аксёнов'),
            ('Варвара Ким', Student.GENDER_FEMALE, 'Подготовительная группа', 'Скрипка', 'Наталья Лебедева'),
            ('Тимур Осипов', Student.GENDER_MALE, 'Подготовительная группа', 'Ударные', 'Алексей Ветров'),
            ('Злата Миронова', Student.GENDER_FEMALE, 'Подготовительная группа', 'Флейта', 'Алексей Ветров'),

            ('Артём Соколов', Student.GENDER_MALE, '1 класс (начинающие)', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Ксения Ильина', Student.GENDER_FEMALE, '1 класс (начинающие)', 'Баян', 'Сергей Аксёнов'),
            ('Павел Громов', Student.GENDER_MALE, '1 класс (начинающие)', 'Гитара', 'Игорь Романов'),
            ('София Фролова', Student.GENDER_FEMALE, '1 класс (начинающие)', 'Вокал', 'Елена Серова'),
            ('Михаил Титов', Student.GENDER_MALE, '1 класс (начинающие)', 'Фортепиано', 'Марина Белова'),
            ('Алиса Рябова', Student.GENDER_FEMALE, '1 класс (начинающие)', 'Домра', 'Сергей Аксёнов'),

            ('Виктория Орлова', Student.GENDER_FEMALE, '2 класс (средний уровень)', 'Вокал', 'Елена Серова'),
            ('Роман Карпов', Student.GENDER_MALE, '2 класс (средний уровень)', 'Гитара', 'Игорь Романов'),
            ('Алина Жукова', Student.GENDER_FEMALE, '2 класс (средний уровень)', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Тимофей Фадеев', Student.GENDER_MALE, '2 класс (средний уровень)', 'Баян', 'Сергей Аксёнов'),
            ('Дарья Никитина', Student.GENDER_FEMALE, '2 класс (средний уровень)', 'Гитара', 'Игорь Романов'),
            ('Семён Крылов', Student.GENDER_MALE, '2 класс (средний уровень)', 'Кларнет', 'Алексей Ветров'),

            ('Никита Мельников', Student.GENDER_MALE, '3 класс (продвинутые)', 'Баян', 'Сергей Аксёнов'),
            ('Полина Егорова', Student.GENDER_FEMALE, '3 класс (продвинутые)', 'Флейта', 'Алексей Ветров'),
            ('Глеб Воронов', Student.GENDER_MALE, '3 класс (продвинутые)', 'Фортепиано', 'Дмитрий Ковалёв'),
            ('Мария Ларионова', Student.GENDER_FEMALE, '3 класс (продвинутые)', 'Вокал', 'Елена Серова'),
            ('Яна Тарасова', Student.GENDER_FEMALE, '3 класс (продвинутые)', 'Домра', 'Сергей Аксёнов'),
            ('Кирилл Гусев', Student.GENDER_MALE, '3 класс (продвинутые)', 'Скрипка', 'Наталья Лебедева'),

            ('Вероника Павлова', Student.GENDER_FEMALE, 'Старший ансамбль', 'Виолончель', 'Наталья Лебедева'),
            ('Егор Комаров', Student.GENDER_MALE, 'Старший ансамбль', 'Саксофон', 'Алексей Ветров'),
            ('Милана Савина', Student.GENDER_FEMALE, 'Старший ансамбль', 'Фортепиано', 'Марина Белова'),
            ('Арсений Фомин', Student.GENDER_MALE, 'Старший ансамбль', 'Аккордеон', 'Сергей Аксёнов'),
            ('Лидия Кузьмина', Student.GENDER_FEMALE, 'Старший ансамбль', 'Хоровая партия', 'Ольга Захарова'),
            ('Степан Захаров', Student.GENDER_MALE, 'Старший ансамбль', 'Балалайка', 'Сергей Аксёнов'),
        ]

        students: list[Student] = []
        specialty_subject = subjects['Специальность']
        education_values = [
            Student.MUSIC_EDUCATION_NONE,
            Student.MUSIC_EDUCATION_SELF,
            Student.MUSIC_EDUCATION_BASIC,
            Student.MUSIC_EDUCATION_SECONDARY,
            Student.MUSIC_EDUCATION_HIGHER,
        ]
        city_church_values = [
            'Тамбов / Центральная церковь',
            'Воронеж / Отрожка',
            'Москва / Северная община',
            'Рязань / Дом молитвы',
            'Липецк / Молодежная группа',
            'Калуга / Музыкальное служение',
        ]
        extra_subject_specs = [
            ('Индивидуальная импровизация', 'Дмитрий Ковалёв'),
            ('Индивидуальное дирижирование', 'Ольга Захарова'),
            ('Индивидуальный оркестр', 'Алексей Ветров'),
            ('Индивидуальная история церковной музыки', 'Ольга Захарова'),
            ('Индивидуальный ансамбль', 'Наталья Лебедева'),
        ]

        for index, (full_name, gender, group_name, instrument_name, specialty_teacher_name) in enumerate(
            student_specs,
            start=1,
        ):
            user_email = f'student{index:02d}@cadet-journal.local'
            user, password = self._create_user_for_full_name(full_name, email=user_email)
            self._assign_user_role(user, self.STUDENT_GROUP_NAME)

            student = Student.objects.create(
                full_name=full_name,
                gender=gender,
                birth_date=date(2008 + index % 8, (index % 12) + 1, min(8 + index, 28)),
                city_church=city_church_values[(index - 1) % len(city_church_values)],
                group=groups[group_name],
                instrument=instruments[instrument_name],
                music_education=education_values[(index - 1) % len(education_values)],
                student_phone=f'+7 (901) 200-00-{index:02d}',
                parent_contacts=(
                    f'Отец ученика {index} - +7 (902) 300-00-{index:02d}\n'
                    f'Мама ученика {index} — +7 (903) 400-00-{index:02d}'
                ),
                comments=(
                    f'Демо-карточка с полными данными. Инструмент: {instrument_name}. '
                    f'Предпочтительное время занятий: {"утро" if index % 2 else "вечер"}. '
                    'Можно использовать для проверки длинных комментариев, переносов строк '
                    'и отображения контактов в админке.'
                ),
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

            extra_subject_name, extra_teacher_name = extra_subject_specs[
                (index - 1) % len(extra_subject_specs)
            ]
            StudentSubject.objects.create(
                student=student,
                subject=subjects[extra_subject_name],
                teacher=teachers[extra_teacher_name],
                is_specialty=False,
                is_active=True,
            )

            if index % 10 == 0:
                StudentSubject.objects.create(
                    student=student,
                    subject=subjects['Индивидуальная гитара'],
                    teacher=teachers['Игорь Романов'],
                    is_specialty=False,
                    is_active=False,
                )

            TemporaryCredential.objects.create(
                login=user.username,
                temporary_password=password,
                student_phone=student.student_phone,
            )
            self._add_credentials('student', full_name, user.username, password)
            students.append(student)

        return students

    def _create_course_group_assignments(
        self,
        academic_year: AcademicYear,
        subjects: dict[str, Subject],
        teachers: dict[str, Teacher],
    ) -> None:
        course_group = StudyGroup.objects.filter(
            name=CourseApplication.STUDENT_COURSE_GROUP_NAME,
            academic_year=academic_year,
        ).first()
        if course_group is None:
            return

        course_group_subjects = [
            ('Сольфеджио', 'Анна Морозова', 10),
            ('Ритмика', 'Алексей Ветров', 20),
            ('Хор', 'Елена Серова', 30),
            ('Ансамбль', 'Наталья Лебедева', 40),
            ('Слушание музыки', 'Анна Морозова', 50),
        ]
        for subject_name, teacher_name, sort_order in course_group_subjects:
            GroupSubject.objects.create(
                group=course_group,
                subject=subjects[subject_name],
                teacher=teachers[teacher_name],
                sort_order=sort_order,
                is_active=True,
            )

        specialty_teachers = [
            'Дмитрий Ковалёв',
            'Елена Серова',
            'Игорь Романов',
            'Наталья Лебедева',
            'Сергей Аксёнов',
        ]
        extra_subjects = [
            ('Индивидуальная импровизация', 'Дмитрий Ковалёв'),
            ('Индивидуальный оркестр', 'Алексей Ветров'),
            ('Индивидуальная история церковной музыки', 'Ольга Захарова'),
        ]
        course_students = course_group.students.filter(is_active=True).order_by('id')

        for index, student in enumerate(course_students, start=1):
            StudentSubject.objects.create(
                student=student,
                subject=subjects['Специальность'],
                teacher=teachers[specialty_teachers[(index - 1) % len(specialty_teachers)]],
                is_specialty=True,
                is_active=True,
            )
            extra_subject_name, extra_teacher_name = extra_subjects[(index - 1) % len(extra_subjects)]
            StudentSubject.objects.create(
                student=student,
                subject=subjects[extra_subject_name],
                teacher=teachers[extra_teacher_name],
                is_specialty=False,
                is_active=True,
            )

    def _create_grades_and_results(self, students: list[Student], academic_year: AcademicYear) -> None:
        rng = Random(2026)
        first_grade_date = academic_year.starts_on + timedelta(days=14)
        grade_values_source = [
            Grade.GRADE_1,
            Grade.GRADE_2,
            Grade.GRADE_3,
            Grade.GRADE_4,
            Grade.GRADE_5,
            Grade.GRADE_ABSENT,
        ]
        grades_to_create: list[Grade] = []
        results_to_create: list[SubjectResult] = []

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

            for subject_index, (subject, teacher) in enumerate(assignment_items, start=1):
                grade_offset = (student.pk + subject_index) % len(grade_values_source)
                grade_values = grade_values_source[grade_offset:] + grade_values_source[:grade_offset]
                for index, grade_value in enumerate(grade_values):
                    grade_date = first_grade_date + timedelta(
                        days=index * 18 + subject_index,
                    )
                    grades_to_create.append(
                        Grade(
                            student=student,
                            subject=subject,
                            teacher=teacher,
                            academic_year=academic_year,
                            date=grade_date,
                            value=grade_value,
                            comment=(
                                f'Демо-оценка: {subject.name.lower()}, '
                                f'занятие {index + 1}, преподаватель {teacher.full_name}.'
                            ),
                        )
                    )

                if subject.final_grade_type == Subject.FINAL_GRADE_TYPE_PASS_FAIL:
                    final_value = rng.choice(['Зачет', 'Зачет', 'Незачет'])
                    exam_value = rng.choice(['Зачет', 'Зачет', 'Незачет'])
                else:
                    final_value = str(rng.choice([3, 4, 4, 5, 5, 'Н']))
                    exam_value = str(rng.choice([3, 4, 4, 5, 5, 'Н']))

                results_to_create.append(
                    SubjectResult(
                        student=student,
                        subject=subject,
                        academic_year=academic_year,
                        exam_grade=exam_value,
                        final_grade=final_value,
                    )
                )

        Grade.objects.bulk_create(grades_to_create, batch_size=500)
        SubjectResult.objects.bulk_create(results_to_create, batch_size=500)

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
                'student_phone': '+7 904 111-22-33',
                'parent_contacts': (
                    'Ольга Смирнова - +7 904 111-22-34\n'
                    'Олег Смирнов — +7 904 111-22-35'
                ),
                'comments': (
                    'Хочет заниматься по субботам. Есть домашняя скрипка, '
                    'нужна консультация по подбору струн.'
                ),
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
                'student_phone': '+7 904 222-33-44',
                'parent_contacts': (
                    'Ирина Кузнецова - +7 904 222-33-45\n'
                    'Игорь Кузнецов - +7 904 222-33-46'
                ),
                'comments': (
                    'Нужен начальный уровень. Родители просят поставить в группу '
                    'с вечерним расписанием.'
                ),
                'status': CourseApplication.STATUS_CONFIRMED,
            },
            {
                'last_name': 'Васильев',
                'first_name': 'Даниил',
                'middle_name': 'Андреевич',
                'gender': CourseApplication.GENDER_MALE,
                'birth_date': date(2011, 9, 18),
                'city_church': 'Саратов / Центральная община',
                'instrument': 'Фортепиано',
                'music_education': CourseApplication.MUSIC_EDUCATION_SECONDARY,
                'student_phone': '+7 904 333-44-55',
                'parent_contacts': (
                    'Мария Васильева - +7 904 333-44-56\n'
                    'Андрей Васильев — +7 904 333-44-57'
                ),
                'comments': (
                    'Уже играет в ансамбле. Интересуется гармонией, чтением с листа '
                    'и подготовкой к итоговому прослушиванию.'
                ),
                'status': CourseApplication.STATUS_CONFIRMED,
            },
            {
                'last_name': 'Мельникова',
                'first_name': 'Таисия',
                'middle_name': 'Романовна',
                'gender': CourseApplication.GENDER_FEMALE,
                'birth_date': date(2012, 1, 27),
                'city_church': 'Калуга / Музыкальное служение',
                'instrument': 'Вокал',
                'music_education': CourseApplication.MUSIC_EDUCATION_HIGHER,
                'student_phone': '+7 904 444-55-66',
                'parent_contacts': (
                    'Нина Мельникова - +7 904 444-55-67\n'
                    'Роман Мельников - +7 904 444-55-68'
                ),
                'comments': (
                    'Нужен индивидуальный вокал и хор. В комментарии специально '
                    'оставлен длинный текст для проверки карточки заявки.'
                ),
                'status': CourseApplication.STATUS_CONFIRMED,
            },
            {
                'last_name': 'Афанасьев',
                'first_name': 'Илья',
                'middle_name': 'Павлович',
                'gender': CourseApplication.GENDER_MALE,
                'birth_date': date(2015, 5, 9),
                'city_church': 'Липецк / Молодежная группа',
                'instrument': 'Ударные',
                'music_education': CourseApplication.MUSIC_EDUCATION_SELF,
                'student_phone': '+7 904 555-66-77',
                'parent_contacts': (
                    'Павел Афанасьев - +7 904 555-66-78\n'
                    'Анна Афанасьева — +7 904 555-66-79'
                ),
                'comments': (
                    'Самостоятельно занимается ритмом. Просит добавить оркестровую '
                    'практику, если будет место в расписании.'
                ),
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
                'student_phone': '+7 904 666-77-88',
                'parent_contacts': (
                    'Сергей Петров - +7 904 666-77-89\n'
                    'Екатерина Петрова - +7 904 666-77-90'
                ),
                'comments': (
                    'Заявка отклонена для проверки логики удаления ученика из журнала '
                    'и очистки временных учетных данных.'
                ),
                'status': CourseApplication.STATUS_REJECTED,
            },
            {
                'last_name': 'Назаров',
                'first_name': 'Марк',
                'middle_name': 'Денисович',
                'gender': CourseApplication.GENDER_MALE,
                'birth_date': date(2016, 8, 14),
                'city_church': 'Тула / Детское служение',
                'instrument': 'Гитара',
                'music_education': CourseApplication.MUSIC_EDUCATION_NONE,
                'student_phone': '+7 904 777-88-99',
                'parent_contacts': (
                    'Денис Назаров - +7 904 777-88-98\n'
                    'Юлия Назарова — +7 904 777-88-97'
                ),
                'comments': (
                    'Отклоненная заявка с полным набором заполненных полей для '
                    'проверки фильтров и карточки заявки.'
                ),
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

    def _create_user_for_full_name(self, full_name: str, *, email: str = ''):
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
            email=email,
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

    def _assign_user_role(self, user, group_name: str) -> None:
        """
        Назначает пользователю роль через группу Django.
        """
        group = self.role_groups[group_name]
        user.groups.add(group)
