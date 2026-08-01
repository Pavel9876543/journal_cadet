from __future__ import annotations

import os
from csv import writer
from datetime import date, timedelta
from pathlib import Path
from random import Random

from django.apps import apps
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Count, Q
from django.utils import timezone

from journal.account_utils import (
    build_username_from_full_name,
    display_name_for_user,
    ensure_temporary_credential_for_user,
    generate_temporary_password,
    split_user_name,
)
from journal.models import (
    AcademicYear,
    AssessmentElement,
    AssessmentGroup,
    AssessmentItem,
    AssessmentResult,
    CourseApplication,
    CourseRegistrationSettings,
    FinalGradeRule,
    Grade,
    GroupSubject,
    Instrument,
    OrchestraPart,
    PasswordRecoveryContact,
    Student,
    StudentAssessmentGroup,
    StudentEnrollment,
    StudentSubject,
    StudyGroup,
    Subject,
    SubjectResult,
    Teacher,
    TeacherEnrollment,
    TeacherSubject,
    TemporaryCredential,
    UserAcademicYearMembership,
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
        parser.add_argument(
            '--allow-production',
            action='store_true',
            help='Явно разрешить разрушительное заполнение в production-окружении.',
        )

    @transaction.atomic
    def handle(self, *args, **options):
        if settings.IS_PRODUCTION_ENV and not options['allow_production']:
            raise CommandError(
                'Команда seed_data удаляет данные и запрещена в production. '
                'Для осознанного запуска добавьте --allow-production.'
            )
        self.credentials: list[dict[str, str]] = []
        self.used_usernames: set[str] = set()
        self.UserModel = get_user_model()

        self._clear_database()
        self.used_usernames = set(self.UserModel.objects.values_list('username', flat=True))

        self.role_groups = self._create_role_groups()
        self._assign_role_to_existing_admins()

        for contact_data in (
            {
                'name': 'Дежурный администратор',
                'phone': '+7 (900) 000-00-01',
                'messengers': 'Telegram, WhatsApp',
                'messenger_username': 'cadet_journal_admin',
                'display_order': 10,
            },
            {
                'name': 'Учебная часть',
                'phone': '+7 (900) 000-00-02',
                'messengers': 'Telegram',
                'messenger_username': 'cadet_study_office',
                'display_order': 20,
            },
        ):
            PasswordRecoveryContact.objects.create(**contact_data)

        # Архивный год создаётся первым: пока он единственный, модель считает
        # его активным и позволяет безопасно сформировать все связанные записи.
        archived_year = self._create_archived_academic_year()
        self._assign_existing_admins_to_academic_year(archived_year)
        CourseRegistrationSettings.objects.update_or_create(
            academic_year=archived_year,
            defaults={
                'telegram_group_url': 'https://t.me/cadet_journal_archive_demo',
                'minimum_registration_age': 14,
                'registration_mode': CourseRegistrationSettings.REGISTRATION_MODE_CLOSED,
            },
        )

        instruments = self._create_instruments()
        self._create_orchestra_parts(instruments)
        subjects = self._create_subjects()
        archived_groups = self._create_groups(archived_year)
        teachers = self._create_teachers(subjects)
        self._create_group_subjects(archived_groups, subjects, teachers)
        archived_students = self._create_students(
            archived_groups,
            instruments,
            subjects,
            teachers,
            academic_year=archived_year,
        )
        self._create_grades_and_results(archived_students, archived_year)
        self._create_assessment_demo_data(
            archived_students,
            archived_year,
            subjects,
            teachers,
        )

        # Более новый год автоматически становится единственным активным.
        academic_year = self._create_current_academic_year()
        self._assign_existing_admins_to_academic_year(academic_year)
        CourseRegistrationSettings.objects.update_or_create(
            academic_year=academic_year,
            defaults={
                'telegram_group_url': 'https://t.me/cadet_journal_demo',
                'minimum_registration_age': 14,
                'registration_mode': CourseRegistrationSettings.REGISTRATION_MODE_OPEN,
            },
        )
        self._assign_teachers_to_academic_year(teachers, academic_year)
        groups = self._create_groups(academic_year)
        self._create_group_subjects(groups, subjects, teachers)
        self._reenroll_students(
            archived_students,
            source_year=archived_year,
            target_year=academic_year,
            groups=groups,
        )
        self._create_course_applications()
        self._create_course_group_assignments(academic_year, subjects, teachers)
        students = list(
            Student.objects
            .filter(
                enrollments__academic_year=academic_year,
                enrollments__is_active=True,
            )
            .select_related('group', 'instrument')
            .distinct()
            .order_by('id')
        )
        self._create_grades_and_results(students, academic_year)
        self._create_assessment_demo_data(students, academic_year, subjects, teachers)

        self._ensure_temporary_credentials_for_all_users()
        self._validate_demo_data()

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
            f'справочных произведений: {AssessmentElement.objects.count()}, '
            f'групп произведений: {AssessmentGroup.objects.count()}, '
            f'произведений: {AssessmentItem.objects.count()}, '
            f'результатов сдачи: {AssessmentResult.objects.count()}, '
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
        self.protected_user_ids = protected_user_ids

        # Сначала удаляем зависимые учебные данные.
        TemporaryCredential.objects.exclude(user_id__in=protected_user_ids).delete()
        PasswordRecoveryContact.objects.all().delete()
        CourseApplication.objects.all().delete()
        AssessmentResult.objects.all().delete()
        SubjectResult.objects.all().delete()
        Grade.objects.all().delete()
        StudentAssessmentGroup.objects.all().delete()
        FinalGradeRule.objects.all().delete()
        AssessmentItem.objects.all().delete()
        AssessmentElement.objects.all().delete()
        AssessmentGroup.objects.all().delete()
        StudentSubject.objects.all().delete()
        GroupSubject.objects.all().delete()
        TeacherSubject.objects.all().delete()
        StudentEnrollment.objects.all().delete()
        TeacherEnrollment.objects.all().delete()
        UserAcademicYearMembership.objects.all().delete()

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
        OrchestraPart.objects.all().delete()
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

    def _assign_existing_admins_to_academic_year(self, academic_year: AcademicYear) -> None:
        memberships = [
            UserAcademicYearMembership(
                user=user,
                academic_year=academic_year,
                is_active=True,
            )
            for user in self.UserModel.objects.filter(is_staff=True, is_active=True)
        ]
        UserAcademicYearMembership.objects.bulk_create(memberships, ignore_conflicts=True)

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
            existing_credential = TemporaryCredential.objects.filter(user=user).first()
            if user.pk in getattr(self, 'protected_user_ids', set()) and existing_credential is None:
                continue
            credential = ensure_temporary_credential_for_user(user)
            if credential is None:
                continue
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

    @staticmethod
    def _demo_academic_year_periods() -> tuple[tuple[date, date], tuple[date, date]]:
        """Return two adjacent, non-overlapping periods of exactly 14 days."""
        today = timezone.localdate()
        active_start = today
        active_end = active_start + timedelta(days=13)
        archived_end = active_start - timedelta(days=1)
        archived_start = archived_end - timedelta(days=13)
        return (archived_start, archived_end), (active_start, active_end)

    @staticmethod
    def _demo_academic_year_name(prefix: str, starts_on: date, ends_on: date) -> str:
        return f'{prefix} {starts_on:%d.%m}-{ends_on:%d.%m.%y}'

    def _create_archived_academic_year(self) -> AcademicYear:
        (starts_on, ends_on), _active_period = self._demo_academic_year_periods()
        return AcademicYear.objects.create(
            name=self._demo_academic_year_name('Арх', starts_on, ends_on),
            starts_on=starts_on,
            ends_on=ends_on,
            is_active=True,
        )

    def _create_current_academic_year(self) -> AcademicYear:
        _archived_period, (starts_on, ends_on) = self._demo_academic_year_periods()
        return AcademicYear.objects.create(
            name=self._demo_academic_year_name('Акт', starts_on, ends_on),
            starts_on=starts_on,
            ends_on=ends_on,
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

    def _create_orchestra_parts(self, instruments: dict[str, Instrument]) -> None:
        parts_by_instrument = {
            'Домра': ('Малая первая', 'Малая вторая', 'Альтовая первая', 'Альтовая вторая'),
            'Балалайка': ('Прима', 'Альт', 'Секунда'),
            'Баян': ('Первый', 'Второй', 'Третий'),
        }
        OrchestraPart.objects.bulk_create(
            OrchestraPart(instrument=instruments[instrument_name], name=part_name)
            for instrument_name, part_names in parts_by_instrument.items()
            for part_name in part_names
        )

    def _create_subjects(self) -> dict[str, Subject]:
        subject_specs = [
            ('Сольфеджио', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Музыкальная литература', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Слушание музыки', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Ритмика', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Хор', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Ансамбль', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Оркестр', Subject.FINAL_GRADE_TYPE_PASS_FAIL, False, Subject.ASSESSMENT_MODE_ELEMENTS),
            ('Фортепиано', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Гитара', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Вокал', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Гармония', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Дирижирование', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Импровизация', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('История церковной музыки', Subject.FINAL_GRADE_TYPE_NUMERIC, False, Subject.ASSESSMENT_MODE_STANDARD),
            ('Специальность', Subject.FINAL_GRADE_TYPE_NUMERIC, True, Subject.ASSESSMENT_MODE_STANDARD),
            ('Индивидуальная импровизация', Subject.FINAL_GRADE_TYPE_NUMERIC, True, Subject.ASSESSMENT_MODE_STANDARD),
            ('Индивидуальное дирижирование', Subject.FINAL_GRADE_TYPE_NUMERIC, True, Subject.ASSESSMENT_MODE_STANDARD),
            ('Индивидуальный оркестр', Subject.FINAL_GRADE_TYPE_PASS_FAIL, True, Subject.ASSESSMENT_MODE_STANDARD),
            ('Индивидуальная история церковной музыки', Subject.FINAL_GRADE_TYPE_NUMERIC, True, Subject.ASSESSMENT_MODE_STANDARD),
            ('Индивидуальный ансамбль', Subject.FINAL_GRADE_TYPE_PASS_FAIL, True, Subject.ASSESSMENT_MODE_STANDARD),
            ('Индивидуальная гитара', Subject.FINAL_GRADE_TYPE_NUMERIC, True, Subject.ASSESSMENT_MODE_STANDARD),
        ]

        return {
            name: Subject.objects.create(
                name=name,
                final_grade_type=final_grade_type,
                is_specialty=is_specialty,
                assessment_mode=assessment_mode,
                is_active=True,
            )
            for name, final_grade_type, is_specialty, assessment_mode in subject_specs
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
                'full_name': 'Морозова Анна Сергеевна',
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
                'full_name': 'Ковалёв Дмитрий Андреевич',
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
                'full_name': 'Серова Елена Викторовна',
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
                'full_name': 'Романов Игорь Павлович',
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
                'full_name': 'Белова Марина Олеговна',
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
                'full_name': 'Аксёнов Сергей Николаевич',
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
                'full_name': 'Лебедева Наталья Игоревна',
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
                'full_name': 'Ветров Алексей Михайлович',
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
                'full_name': 'Захарова Ольга Петровна',
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
                user=user,
                login=user.username,
                temporary_password=password,
            )
            self._add_credentials('teacher', full_name, user.username, password)
            teachers[full_name] = teacher

        return teachers

    def _assign_teachers_to_academic_year(
        self,
        teachers: dict[str, Teacher],
        academic_year: AcademicYear,
    ) -> None:
        for teacher in teachers.values():
            TeacherEnrollment.objects.create(
                teacher=teacher,
                academic_year=academic_year,
                is_active=True,
            )

    def _create_group_subjects(
        self,
        groups: dict[str, StudyGroup],
        subjects: dict[str, Subject],
        teachers: dict[str, Teacher],
    ) -> None:
        assignment_specs = [
            ('Подготовительная группа', 'Ритмика', 'Ветров Алексей Михайлович', 10, True),
            ('Подготовительная группа', 'Слушание музыки', 'Морозова Анна Сергеевна', 20, True),
            ('Подготовительная группа', 'Хор', 'Серова Елена Викторовна', 30, True),
            ('Подготовительная группа', 'Фортепиано', 'Ковалёв Дмитрий Андреевич', 40, True),

            ('1 класс (начинающие)', 'Ритмика', 'Ветров Алексей Михайлович', 10, True),
            ('1 класс (начинающие)', 'Сольфеджио', 'Морозова Анна Сергеевна', 20, True),
            ('1 класс (начинающие)', 'Слушание музыки', 'Морозова Анна Сергеевна', 30, True),
            ('1 класс (начинающие)', 'Хор', 'Белова Марина Олеговна', 40, True),
            ('1 класс (начинающие)', 'Фортепиано', 'Ковалёв Дмитрий Андреевич', 50, True),

            ('2 класс (средний уровень)', 'Сольфеджио', 'Серова Елена Викторовна', 10, True),
            ('2 класс (средний уровень)', 'Музыкальная литература', 'Романов Игорь Павлович', 20, True),
            ('2 класс (средний уровень)', 'Хор', 'Белова Марина Олеговна', 30, True),
            ('2 класс (средний уровень)', 'Ансамбль', 'Романов Игорь Павлович', 40, True),
            ('2 класс (средний уровень)', 'Гитара', 'Романов Игорь Павлович', 50, True),
            ('2 класс (средний уровень)', 'Импровизация', 'Аксёнов Сергей Николаевич', 60, True),
            ('2 класс (средний уровень)', 'Оркестр', 'Ветров Алексей Михайлович', 70, True),

            ('3 класс (продвинутые)', 'Сольфеджио', 'Аксёнов Сергей Николаевич', 10, True),
            ('3 класс (продвинутые)', 'Гармония', 'Ковалёв Дмитрий Андреевич', 20, True),
            ('3 класс (продвинутые)', 'Музыкальная литература', 'Белова Марина Олеговна', 30, True),
            ('3 класс (продвинутые)', 'Ансамбль', 'Аксёнов Сергей Николаевич', 40, True),
            ('3 класс (продвинутые)', 'Оркестр', 'Лебедева Наталья Игоревна', 50, True),
            ('3 класс (продвинутые)', 'Дирижирование', 'Захарова Ольга Петровна', 60, True),

            ('Старший ансамбль', 'Сольфеджио', 'Захарова Ольга Петровна', 10, True),
            ('Старший ансамбль', 'Гармония', 'Ковалёв Дмитрий Андреевич', 20, True),
            ('Старший ансамбль', 'История церковной музыки', 'Захарова Ольга Петровна', 30, True),
            ('Старший ансамбль', 'Оркестр', 'Романов Игорь Павлович', 40, True),
            ('Старший ансамбль', 'Ансамбль', 'Лебедева Наталья Игоревна', 50, True),
            ('Старший ансамбль', 'Дирижирование', 'Захарова Ольга Петровна', 60, True),

            ('Архивная группа', 'Сольфеджио', 'Морозова Анна Сергеевна', 10, False),
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
        *,
        academic_year: AcademicYear,
    ) -> list[Student]:
        student_specs = [
            ('Андреев Лев Максимович', Student.GENDER_MALE, 'Подготовительная группа', 'Фортепиано', 'Ковалёв Дмитрий Андреевич'),
            ('Богданова Ева Ильинична', Student.GENDER_FEMALE, 'Подготовительная группа', 'Вокал', 'Серова Елена Викторовна'),
            ('Денисов Матвей Олегович', Student.GENDER_MALE, 'Подготовительная группа', 'Баян', 'Аксёнов Сергей Николаевич'),
            ('Ким Варвара Романовна', Student.GENDER_FEMALE, 'Подготовительная группа', 'Скрипка', 'Лебедева Наталья Игоревна'),
            ('Осипов Тимур Артёмович', Student.GENDER_MALE, 'Подготовительная группа', 'Ударные', 'Ветров Алексей Михайлович'),
            ('Миронова Злата Павловна', Student.GENDER_FEMALE, 'Подготовительная группа', 'Флейта', 'Ветров Алексей Михайлович'),

            ('Соколов Артём Денисович', Student.GENDER_MALE, '1 класс (начинающие)', 'Фортепиано', 'Ковалёв Дмитрий Андреевич'),
            ('Ильина Ксения Андреевна', Student.GENDER_FEMALE, '1 класс (начинающие)', 'Баян', 'Аксёнов Сергей Николаевич'),
            ('Громов Павел Игоревич', Student.GENDER_MALE, '1 класс (начинающие)', 'Гитара', 'Романов Игорь Павлович'),
            ('Фролова София Максимовна', Student.GENDER_FEMALE, '1 класс (начинающие)', 'Вокал', 'Серова Елена Викторовна'),
            ('Титов Михаил Сергеевич', Student.GENDER_MALE, '1 класс (начинающие)', 'Фортепиано', 'Белова Марина Олеговна'),
            ('Рябова Алиса Олеговна', Student.GENDER_FEMALE, '1 класс (начинающие)', 'Домра', 'Аксёнов Сергей Николаевич'),

            ('Орлова Виктория Романовна', Student.GENDER_FEMALE, '2 класс (средний уровень)', 'Вокал', 'Серова Елена Викторовна'),
            ('Карпов Роман Павлович', Student.GENDER_MALE, '2 класс (средний уровень)', 'Гитара', 'Романов Игорь Павлович'),
            ('Жукова Алина Денисовна', Student.GENDER_FEMALE, '2 класс (средний уровень)', 'Фортепиано', 'Ковалёв Дмитрий Андреевич'),
            ('Фадеев Тимофей Ильич', Student.GENDER_MALE, '2 класс (средний уровень)', 'Баян', 'Аксёнов Сергей Николаевич'),
            ('Никитина Дарья Андреевна', Student.GENDER_FEMALE, '2 класс (средний уровень)', 'Гитара', 'Романов Игорь Павлович'),
            ('Крылов Семён Максимович', Student.GENDER_MALE, '2 класс (средний уровень)', 'Кларнет', 'Ветров Алексей Михайлович'),

            ('Мельников Никита Олегович', Student.GENDER_MALE, '3 класс (продвинутые)', 'Баян', 'Аксёнов Сергей Николаевич'),
            ('Егорова Полина Игоревна', Student.GENDER_FEMALE, '3 класс (продвинутые)', 'Флейта', 'Ветров Алексей Михайлович'),
            ('Воронов Глеб Романович', Student.GENDER_MALE, '3 класс (продвинутые)', 'Фортепиано', 'Ковалёв Дмитрий Андреевич'),
            ('Ларионова Мария Павловна', Student.GENDER_FEMALE, '3 класс (продвинутые)', 'Вокал', 'Серова Елена Викторовна'),
            ('Тарасова Яна Сергеевна', Student.GENDER_FEMALE, '3 класс (продвинутые)', 'Домра', 'Аксёнов Сергей Николаевич'),
            ('Гусев Кирилл Андреевич', Student.GENDER_MALE, '3 класс (продвинутые)', 'Скрипка', 'Лебедева Наталья Игоревна'),

            ('Павлова Вероника Денисовна', Student.GENDER_FEMALE, 'Старший ансамбль', 'Виолончель', 'Лебедева Наталья Игоревна'),
            ('Комаров Егор Ильич', Student.GENDER_MALE, 'Старший ансамбль', 'Саксофон', 'Ветров Алексей Михайлович'),
            ('Савина Милана Олеговна', Student.GENDER_FEMALE, 'Старший ансамбль', 'Фортепиано', 'Белова Марина Олеговна'),
            ('Фомин Арсений Романович', Student.GENDER_MALE, 'Старший ансамбль', 'Аккордеон', 'Аксёнов Сергей Николаевич'),
            ('Кузьмина Лидия Павловна', Student.GENDER_FEMALE, 'Старший ансамбль', 'Хоровая партия', 'Захарова Ольга Петровна'),
            ('Захаров Степан Сергеевич', Student.GENDER_MALE, 'Старший ансамбль', 'Балалайка', 'Аксёнов Сергей Николаевич'),
        ]

        # Берём по три ученика на каждую основную группу. Эти же карточки затем
        # зачисляются в следующий учебный год, сохраняя архивные снимки данных.
        positions: dict[str, int] = {}
        compact_specs = []
        for student_spec in student_specs:
            group_name = student_spec[2]
            position = positions.get(group_name, 0)
            positions[group_name] = position + 1
            if position < 3:
                compact_specs.append(student_spec)

        students: list[Student] = []
        specialty_subject = subjects['Специальность']
        education_values = [
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
            ('Индивидуальная импровизация', 'Ковалёв Дмитрий Андреевич'),
            ('Индивидуальное дирижирование', 'Захарова Ольга Петровна'),
            ('Индивидуальный оркестр', 'Ветров Алексей Михайлович'),
            ('Индивидуальная история церковной музыки', 'Захарова Ольга Петровна'),
            ('Индивидуальный ансамбль', 'Лебедева Наталья Игоревна'),
        ]

        for index, (full_name, gender, group_name, instrument_name, specialty_teacher_name) in enumerate(
            compact_specs,
            start=1,
        ):
            user_email = f'student{index:02d}@cadet-journal.local'
            user, password = self._create_user_for_full_name(full_name, email=user_email)
            self._assign_user_role(user, self.STUDENT_GROUP_NAME)
            instrument = instruments[instrument_name]
            orchestra_part = (
                instrument.orchestra_parts.filter(is_active=True).order_by('name').first()
            )

            student = Student.objects.create(
                full_name=full_name,
                gender=gender,
                birth_date=date(2008 + index % 8, (index % 12) + 1, min(8 + index, 28)),
                city_church=city_church_values[(index - 1) % len(city_church_values)],
                group=groups[group_name],
                instrument=instrument,
                orchestra_part=orchestra_part,
                music_education=education_values[(index - 1) % len(education_values)],
                student_phone=f'+7 (901) 200-00-{index:02d}',
                parent_contacts=(
                    f'Отец ученика {index} — +7 (902) 300-00-{index:02d}\n'
                    f'Мама ученика {index} — +7 (903) 400-00-{index:02d}'
                ),
                comments=(
                    f'Демо-карточка общего набора. Инструмент: {instrument_name}. '
                    f'Предпочтительное время занятий: {"утро" if index % 2 else "вечер"}. '
                    'Эта карточка используется в обоих демонстрационных учебных годах.'
                ),
                user=user,
                is_active=True,
            )
            StudentSubject.objects.create(
                student=student,
                subject=specialty_subject,
                teacher=teachers[specialty_teacher_name],
                academic_year=academic_year,
                is_active=True,
            )

            extra_subject_name, extra_teacher_name = extra_subject_specs[
                (index - 1) % len(extra_subject_specs)
            ]
            StudentSubject.objects.create(
                student=student,
                subject=subjects[extra_subject_name],
                teacher=teachers[extra_teacher_name],
                academic_year=academic_year,
                is_active=True,
            )

            if index % 10 == 0:
                StudentSubject.objects.create(
                    student=student,
                    subject=subjects['Индивидуальная гитара'],
                    teacher=teachers['Романов Игорь Павлович'],
                    academic_year=academic_year,
                    is_active=False,
                )

            TemporaryCredential.objects.create(
                user=user,
                login=user.username,
                temporary_password=password,
                student_phone=student.student_phone,
            )
            self._add_credentials('student', full_name, user.username, password)
            students.append(student)

        return students

    def _reenroll_students(
        self,
        students: list[Student],
        *,
        source_year: AcademicYear,
        target_year: AcademicYear,
        groups: dict[str, StudyGroup],
    ) -> None:
        """Reuse the same student profiles while creating a new yearly history."""
        for student in students:
            source_enrollment = StudentEnrollment.objects.select_related('group').get(
                student=student,
                academic_year=source_year,
            )
            source_assignments = list(
                StudentSubject.objects.filter(
                    student=student,
                    academic_year=source_year,
                ).select_related('subject', 'teacher')
            )
            if source_enrollment.group is None:
                raise CommandError(f'У ученика {student} отсутствует архивная группа.')

            student.group = groups[source_enrollment.group.name]
            student.save(update_fields=['group'])

            for assignment in source_assignments:
                StudentSubject.objects.create(
                    student=student,
                    subject=assignment.subject,
                    teacher=assignment.teacher,
                    academic_year=target_year,
                    is_active=assignment.is_active,
                )

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
            ('Сольфеджио', 'Морозова Анна Сергеевна', 10),
            ('Ритмика', 'Ветров Алексей Михайлович', 20),
            ('Хор', 'Серова Елена Викторовна', 30),
            ('Ансамбль', 'Лебедева Наталья Игоревна', 40),
            ('Слушание музыки', 'Морозова Анна Сергеевна', 50),
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
            'Ковалёв Дмитрий Андреевич',
            'Серова Елена Викторовна',
            'Романов Игорь Павлович',
            'Лебедева Наталья Игоревна',
            'Аксёнов Сергей Николаевич',
        ]
        extra_subjects = [
            ('Индивидуальная импровизация', 'Ковалёв Дмитрий Андреевич'),
            ('Индивидуальный оркестр', 'Ветров Алексей Михайлович'),
            ('Индивидуальная история церковной музыки', 'Захарова Ольга Петровна'),
        ]
        course_students = course_group.students.filter(is_active=True).order_by('id')

        for index, student in enumerate(course_students, start=1):
            StudentSubject.objects.create(
                student=student,
                subject=subjects['Специальность'],
                teacher=teachers[specialty_teachers[(index - 1) % len(specialty_teachers)]],
                is_active=True,
            )
            extra_subject_name, extra_teacher_name = extra_subjects[(index - 1) % len(extra_subjects)]
            StudentSubject.objects.create(
                student=student,
                subject=subjects[extra_subject_name],
                teacher=teachers[extra_teacher_name],
                is_active=True,
            )

    def _create_grades_and_results(self, students: list[Student], academic_year: AcademicYear) -> None:
        rng = Random(2026 + academic_year.starts_on.toordinal())
        period_days = (academic_year.ends_on - academic_year.starts_on).days
        grade_offsets = (1, max(2, period_days // 2), max(3, period_days - 1))
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
            enrollment = StudentEnrollment.objects.select_related('group').get(
                student=student,
                academic_year=academic_year,
            )
            group_assignments = list(
                GroupSubject.objects
                .select_related('subject', 'teacher')
                .filter(group=enrollment.group, is_active=True)
                .order_by('sort_order', 'subject__name')
            )
            individual_assignments = list(
                StudentSubject.objects
                .select_related('subject', 'teacher')
                .filter(student=student, academic_year=academic_year, is_active=True)
                .order_by('subject__name')
            )

            assignment_items = [
                (assignment.subject, assignment.teacher)
                for assignment in group_assignments
            ]
            assigned_subject_ids = {subject.id for subject, _teacher in assignment_items}
            assignment_items.extend(
                (assignment.subject, assignment.teacher)
                for assignment in individual_assignments
                if assignment.subject_id not in assigned_subject_ids
            )

            for subject_index, (subject, teacher) in enumerate(assignment_items, start=1):
                if subject.uses_element_assessment:
                    continue
                grade_offset = (student.pk + subject_index) % len(grade_values_source)
                grade_values = grade_values_source[grade_offset:] + grade_values_source[:grade_offset]
                for index, grade_value in enumerate(grade_values[:3]):
                    grade_date = academic_year.starts_on + timedelta(days=grade_offsets[index])
                    grades_to_create.append(
                        Grade(
                            student=student,
                            subject=subject,
                            teacher=teacher,
                            academic_year=academic_year,
                            enrollment=enrollment,
                            date=grade_date,
                            value=grade_value,
                            student_name_snapshot=enrollment.full_name,
                            group_name_snapshot=enrollment.group.name if enrollment.group else '',
                            subject_name_snapshot=subject.name,
                            teacher_name_snapshot=teacher.full_name,
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
                        enrollment=enrollment,
                        exam_grade=exam_value,
                        final_grade=final_value,
                        student_name_snapshot=enrollment.full_name,
                        group_name_snapshot=enrollment.group.name if enrollment.group else '',
                        subject_name_snapshot=subject.name,
                        final_grade_type_snapshot=subject.final_grade_type,
                    )
                )

        Grade.objects.bulk_create(grades_to_create, batch_size=500)
        SubjectResult.objects.bulk_create(results_to_create, batch_size=500)

    def _create_assessment_demo_data(
        self,
        students: list[Student],
        academic_year: AcademicYear,
        subjects: dict[str, Subject],
        teachers: dict[str, Teacher],
    ) -> None:
        subject = subjects['Оркестр']
        group_specs = [
            {
                'name': 'Младший оркестр',
                'study_group_name': '2 класс (средний уровень)',
                'teacher': teachers['Ветров Алексей Михайлович'],
                'sort_order': 10,
                'items': [
                    ('Ритмический этюд', True),
                    ('Марш юных музыкантов', True),
                    ('Короткая импровизация', False),
                ],
            },
            {
                'name': 'Продвинутый оркестр',
                'study_group_name': '3 класс (продвинутые)',
                'teacher': teachers['Лебедева Наталья Игоревна'],
                'sort_order': 20,
                'items': [
                    ('Торжественная увертюра', True),
                    ('Пастораль', True),
                    ('Финальный марш', True),
                ],
            },
            {
                'name': 'Старший оркестр',
                'study_group_name': 'Старший ансамбль',
                'teacher': teachers['Романов Игорь Павлович'],
                'sort_order': 30,
                'items': [
                    ('Симфоническая миниатюра', True),
                    ('Хорал', True),
                    ('Праздничная фантазия', True),
                ],
            },
        ]

        # Общие правила покрывают и учеников, назначенных сразу в несколько
        # групп произведений. Строковые значения специально разнообразны.
        grade_by_passed_count = {
            0: 'N',
            1: '3',
            2: '4',
            3: '5',
            4: '5+',
            5: '5+',
        }
        for passed_count, grade in grade_by_passed_count.items():
            FinalGradeRule.objects.create(
                subject=subject,
                academic_year=academic_year,
                rule_type=FinalGradeRule.RULE_COUNT,
                passed_count=passed_count,
                grade=grade,
                priority=20 + passed_count,
            )
        FinalGradeRule.objects.create(
            subject=subject,
            academic_year=academic_year,
            rule_type=FinalGradeRule.RULE_DEFAULT,
            grade='Не рассчитано',
            priority=999,
        )

        students_by_group: dict[str, list[Student]] = {}
        for student in students:
            if student.group_id:
                students_by_group.setdefault(student.group.name, []).append(student)

        created_groups: dict[str, AssessmentGroup] = {}
        for group_spec in group_specs:
            assessment_group = AssessmentGroup.objects.create(
                name=group_spec['name'],
                description=(
                    'Демонстрационная группа произведений. Она отделена от учебной '
                    'группы: ученики назначаются в неё независимо и могут состоять '
                    'сразу в нескольких группах произведений.'
                ),
                subject=subject,
                academic_year=academic_year,
                sort_order=group_spec['sort_order'],
            )
            created_groups[group_spec['name']] = assessment_group
            items = [
                AssessmentItem.objects.create(
                    title=title,
                    description=(
                        'Демонстрационное произведение для проверки кабинетов, '
                        'административных вкладок и автоматического расчёта итогов.'
                    ),
                    subject=subject,
                    academic_year=academic_year,
                    group=assessment_group,
                    responsible_teacher=group_spec['teacher'],
                    sort_order=index * 10,
                    is_required=is_required,
                )
                for index, (title, is_required) in enumerate(group_spec['items'], start=1)
            ]

            assigned_students = students_by_group.get(group_spec['study_group_name'], [])
            for student_index, student in enumerate(assigned_students, start=1):
                assignment = StudentAssessmentGroup.objects.create(
                    student=student,
                    assessment_group=assessment_group,
                    academic_year=academic_year,
                )
                for item_index, item in enumerate(items, start=1):
                    # Часть строк намеренно отсутствует и отображается как «Не оценено».
                    if (student_index + item_index) % 5 == 0:
                        continue
                    status = (
                        AssessmentResult.STATUS_PASSED
                        if (student_index + item_index) % 4 != 0
                        else AssessmentResult.STATUS_FAILED
                    )
                    AssessmentResult.objects.create(
                        enrollment=assignment.enrollment,
                        item=item,
                        status=status,
                        assessed_by=group_spec['teacher'],
                        comment=(
                            'Демонстрационный результат: '
                            + (
                                'произведение принято.'
                                if status == AssessmentResult.STATUS_PASSED
                                else 'требуется повторная сдача.'
                            )
                        ),
                    )

        # Для младшего состава показываем отдельные правила «все обязательные
        # произведения», чтобы в интерфейсе были представлены оба типа правил.
        junior_group = created_groups['Младший оркестр']
        FinalGradeRule.objects.create(
            subject=subject,
            academic_year=academic_year,
            assessment_group=junior_group,
            rule_type=FinalGradeRule.RULE_ALL_REQUIRED,
            condition_value=True,
            grade='Зачёт',
            priority=1,
        )
        FinalGradeRule.objects.create(
            subject=subject,
            academic_year=academic_year,
            assessment_group=junior_group,
            rule_type=FinalGradeRule.RULE_ALL_REQUIRED,
            condition_value=False,
            grade='Незачёт',
            priority=2,
        )

        # Сводный состав демонстрирует отношение «ученик — несколько групп».
        combined_group = AssessmentGroup.objects.create(
            name='Праздничный сводный состав',
            description=(
                'Дополнительная группа для учеников из разных учебных групп. '
                'Используется для проверки объединения произведений в кабинетах.'
            ),
            subject=subject,
            academic_year=academic_year,
            sort_order=40,
        )
        combined_items = [
            AssessmentItem.objects.create(
                title='Общий праздничный номер',
                description='Обязательное произведение сводного состава.',
                subject=subject,
                academic_year=academic_year,
                group=combined_group,
                responsible_teacher=teachers['Лебедева Наталья Игоревна'],
                sort_order=10,
                is_required=True,
            ),
            AssessmentItem.objects.create(
                title='Выход на бис',
                description='Дополнительное произведение сводного состава.',
                subject=subject,
                academic_year=academic_year,
                group=combined_group,
                responsible_teacher=teachers['Романов Игорь Павлович'],
                sort_order=20,
                is_required=False,
            ),
        ]
        combined_students = (
            students_by_group.get('3 класс (продвинутые)', [])[:2]
            + students_by_group.get('Старший ансамбль', [])[:2]
        )
        for student_index, student in enumerate(combined_students, start=1):
            assignment = StudentAssessmentGroup.objects.create(
                student=student,
                assessment_group=combined_group,
                academic_year=academic_year,
            )
            for item_index, item in enumerate(combined_items, start=1):
                if student_index == 2 and item_index == 2:
                    continue
                status = (
                    AssessmentResult.STATUS_FAILED
                    if student_index == 3 and item_index == 1
                    else AssessmentResult.STATUS_PASSED
                )
                AssessmentResult.objects.create(
                    enrollment=assignment.enrollment,
                    item=item,
                    status=status,
                    assessed_by=item.responsible_teacher,
                    comment=(
                        'Сводный состав: результат сохранён для демонстрации '
                        'каскадной работы с данными.'
                    ),
                )

    def _validate_demo_data(self) -> None:
        errors: list[str] = []
        years = list(AcademicYear.objects.order_by('starts_on'))
        active_year = next((year for year in years if year.is_active), None)

        if len(years) != 2:
            errors.append('Должно быть создано ровно два учебных года.')
        if AcademicYear.objects.filter(is_active=True).count() != 1:
            errors.append('Должен быть ровно один активный учебный год.')
        if active_year is None:
            errors.append('Не найден активный учебный год.')
        elif active_year != max(years, key=lambda year: (year.starts_on, year.pk)):
            errors.append('Активным должен быть самый новый демонстрационный учебный год.')

        for academic_year in years:
            if (academic_year.ends_on - academic_year.starts_on).days != 13:
                errors.append(f'Учебный период {academic_year} должен длиться ровно 14 дней.')
        for previous, current in zip(years, years[1:]):
            if previous.ends_on >= current.starts_on:
                errors.append(f'Учебные периоды {previous} и {current} пересекаются.')

        shared_students_count = (
            StudentEnrollment.objects
            .values('student_id')
            .annotate(years_count=Count('academic_year_id', distinct=True))
            .filter(years_count=2)
            .count()
        )
        if shared_students_count < 15:
            errors.append('Одни и те же демонстрационные ученики должны быть зачислены в оба года.')

        for academic_year in years:
            if not CourseRegistrationSettings.objects.filter(
                academic_year=academic_year,
            ).exists():
                errors.append(f'Нет настроек регистрации для {academic_year}.')
            if not StudyGroup.objects.filter(academic_year=academic_year).exists():
                errors.append(f'Нет учебных групп для {academic_year}.')
            if not Grade.objects.filter(academic_year=academic_year).exists():
                errors.append(f'Нет оценок для {academic_year}.')
            if not SubjectResult.objects.filter(academic_year=academic_year).exists():
                errors.append(f'Нет итогов для {academic_year}.')
            if not AssessmentGroup.objects.filter(academic_year=academic_year).exists():
                errors.append(f'Нет групп произведений для {academic_year}.')
            if not AssessmentItem.objects.filter(group__academic_year=academic_year).exists():
                errors.append(f'Нет произведений для {academic_year}.')
            if not StudentAssessmentGroup.objects.filter(assessment_group__academic_year=academic_year).exists():
                errors.append(f'Нет назначений произведений для {academic_year}.')
            if Grade.objects.filter(academic_year=academic_year).filter(
                Q(date__lt=academic_year.starts_on) | Q(date__gt=academic_year.ends_on),
            ).exists():
                errors.append(f'Есть оценки вне дат учебного года {academic_year}.')
            oversized_group = (
                StudentEnrollment.objects
                .filter(academic_year=academic_year, is_active=True, group__isnull=False)
                .values('group_id')
                .annotate(total=Count('id'))
                .filter(total__gt=3)
                .exists()
            )
            if oversized_group:
                errors.append(f'В {academic_year} есть группа более чем с тремя учениками.')

        if active_year and CourseApplication.objects.exclude(academic_year=active_year).exists():
            errors.append('Демонстрационные заявки должны относиться к активному учебному году.')

        if Grade.objects.filter(subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS).exists():
            errors.append('Для предмета со сдачей произведений созданы обычные оценки.')

        if not AssessmentGroup.objects.exists() or not AssessmentItem.objects.exists():
            errors.append('Не созданы демонстрационные группы произведений и произведения.')

        if not StudentAssessmentGroup.objects.exists() or not AssessmentResult.objects.exists():
            errors.append('Не созданы назначения групп произведений или результаты сдачи.')

        if not AssessmentItem.objects.filter(is_required=False).exists():
            errors.append('Не созданы дополнительные необязательные произведения.')

        if not FinalGradeRule.objects.filter(
            rule_type=FinalGradeRule.RULE_ALL_REQUIRED,
        ).exists():
            errors.append('Не созданы правила итогов по обязательным произведениям.')

        if not Student.objects.annotate(
            active_assessment_groups=Count(
                'assessment_group_assignments',
                filter=Q(assessment_group_assignments__is_active=True),
                distinct=True,
            ),
        ).filter(active_assessment_groups__gte=2).exists():
            errors.append('Нет ученика, назначенного в несколько групп произведений.')

        if not AssessmentResult.objects.filter(status=AssessmentResult.STATUS_PASSED).exists():
            errors.append('Не созданы результаты со статусом «Зачёт».')

        if not AssessmentResult.objects.filter(status=AssessmentResult.STATUS_FAILED).exists():
            errors.append('Не созданы результаты со статусом «Незачёт».')

        for result in AssessmentResult.objects.select_related(
            'enrollment', 'item__group', 'item__responsible_teacher'
        ):
            assignment_exists = StudentAssessmentGroup.objects.filter(
                student_id=result.enrollment.student_id,
                assessment_group_id=result.item.group_id,
                assessment_group__academic_year_id=result.item.group.academic_year_id,
                enrollment_id=result.enrollment_id,
                is_active=True,
            ).exists()
            if not assignment_exists:
                errors.append(
                    f'Результат сдачи #{result.pk} не имеет активного назначения '
                    'ученика в группу произведений.'
                )
                break
            if result.assessed_by_id != result.item.responsible_teacher_id:
                errors.append(
                    f'Результат сдачи #{result.pk} выставлен не ответственным преподавателем.'
                )
                break

        teacher_links = []
        teacher_links.extend(
            (row.teacher_id, row.subject_id, row.group.academic_year_id)
            for row in GroupSubject.objects.select_related('group').filter(is_active=True)
        )
        teacher_links.extend(
            (row.teacher_id, row.subject_id, row.academic_year_id)
            for row in StudentSubject.objects.filter(is_active=True)
        )
        teacher_links.extend(
            (row.responsible_teacher_id, row.subject_id, row.academic_year_id)
            for row in AssessmentItem.objects.filter(
                is_active=True, responsible_teacher__isnull=False
            )
        )
        for teacher_id, subject_id, year_id in teacher_links:
            if not TeacherEnrollment.objects.filter(
                teacher_id=teacher_id, academic_year_id=year_id, is_active=True
            ).exists():
                errors.append(
                    f'У преподавателя #{teacher_id} отсутствует активное участие '
                    f'в учебном году #{year_id}.'
                )
                break
            if not TeacherSubject.objects.filter(
                teacher_id=teacher_id, subject_id=subject_id
            ).exists():
                errors.append(
                    f'У преподавателя #{teacher_id} отсутствует связь с предметом #{subject_id}.'
                )
                break

        if SubjectResult.objects.filter(
            subject__assessment_mode=Subject.ASSESSMENT_MODE_ELEMENTS,
            is_auto_calculated=False,
        ).exists():
            errors.append('Итоги специального режима должны рассчитываться автоматически.')

        if GroupSubject.objects.filter(subject__is_specialty=True).exists():
            errors.append('Индивидуальные предметы назначены группам.')

        if StudentSubject.objects.filter(subject__is_specialty=False).exists():
            errors.append('Групповые предметы назначены индивидуальным ученикам.')

        students_without_individual_subjects = (
            Student.objects
            .filter(is_active=True)
            .annotate(
                active_individual_subjects=Count(
                    'individual_subjects',
                    filter=Q(
                        individual_subjects__is_active=True,
                        individual_subjects__subject__is_specialty=True,
                    ),
                ),
            )
            .filter(active_individual_subjects=0)
        )
        if students_without_individual_subjects.exists():
            errors.append('У некоторых активных учеников нет индивидуального предмета.')

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
        for grade_id, student_id, group_id, subject_id, teacher_id in Grade.objects.values_list(
            'pk',
            'student_id',
            'enrollment__group_id',
            'subject_id',
            'teacher_id',
        ):
            if (
                (group_id, subject_id, teacher_id) not in group_grade_keys
                and (student_id, subject_id, teacher_id) not in individual_grade_keys
            ):
                errors.append(f'Оценка без назначения: #{grade_id}.')
                break

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
        for result_id, student_id, group_id, subject_id, exam_grade, final_grade in SubjectResult.objects.values_list(
            'pk',
            'student_id',
            'enrollment__group_id',
            'subject_id',
            'exam_grade',
            'final_grade',
        ):
            if (group_id, subject_id) not in group_result_keys and (student_id, subject_id) not in individual_result_keys:
                errors.append(f'Итог без назначения: #{result_id}.')
                break

        for model in apps.get_app_config('journal').get_models():
            for field in model._meta.concrete_fields:
                if field.primary_key or field.null or field.blank:
                    continue
                if field.get_internal_type() not in {
                    'CharField', 'TextField', 'EmailField', 'SlugField', 'URLField',
                }:
                    continue
                if model.objects.filter(**{field.name: ''}).exists():
                    errors.append(
                        f'Пустое обязательное поле {model._meta.label}.{field.name}.'
                    )
                    break

        if self.UserModel.objects.filter(username='').exists():
            errors.append('В тестовых данных есть пользователь без логина.')

        if errors:
            raise CommandError('Тестовые данные созданы с противоречиями: ' + ' '.join(errors))

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
                'instrument': 'Гусли',
                'music_education': CourseApplication.MUSIC_EDUCATION_NONE,
                'student_phone': '+7 904 222-33-44',
                'parent_contacts': (
                    'Ирина Кузнецова - +7 904 222-33-45\n'
                    'Игорь Кузнецов - +7 904 222-33-46'
                ),
                'comments': (
                    'Нужен начальный уровень на собственном инструменте. Родители просят поставить в группу '
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
        application_specs = [application_specs[0], application_specs[1], application_specs[-1]]

        for application_data in application_specs:
            application_data = dict(application_data)

            instrument_name = (application_data.get('instrument') or '').strip()
            instrument_reference = (
                Instrument.objects
                .filter(name=instrument_name)
                .first()
            )

            application_data.update(
                instrument=instrument_name,
                instrument_reference=instrument_reference,
                custom_instrument='' if instrument_reference else instrument_name,
            )

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
