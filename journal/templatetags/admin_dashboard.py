from __future__ import annotations

from datetime import timedelta
from urllib.parse import urlencode

from django import template
from django.urls import NoReverseMatch, reverse
from django.utils import timezone

from journal.academic_year_context import get_selected_admin_academic_year
from journal.models import (
    AcademicYear,
    CourseApplication,
    Grade,
    PasswordRecoveryContact,
    Student,
    StudyGroup,
    Subject,
    Teacher,
    TemporaryCredential,
)


register = template.Library()


def _can(user, permission: str | None) -> bool:
    if permission == 'superuser':
        return bool(user and user.is_active and user.is_superuser)
    if permission is None:
        return bool(user and user.is_staff)
    return bool(user and user.has_perm(permission))


def _reverse(url_name: str, params: dict | None = None) -> str:
    try:
        url = reverse(url_name)
    except NoReverseMatch:
        return '#'

    if params:
        return f'{url}?{urlencode(params)}'
    return url


def _admin_url(app_label: str, model_name: str, action: str = 'changelist', params: dict | None = None) -> str:
    return _reverse(f'admin:{app_label}_{model_name}_{action}', params=params)


def _item(title: str, url: str, icon: str, note: str, user, permission: str | None = None) -> dict | None:
    if not _can(user, permission):
        return None
    return {
        'title': title,
        'url': url,
        'icon': icon,
        'note': note,
    }


def _section(title: str, description: str, items: list[dict | None]) -> dict | None:
    visible_items = [item for item in items if item is not None]
    if not visible_items:
        return None
    return {
        'title': title,
        'description': description,
        'items': visible_items,
    }


def _stat(label: str, value, url: str, icon: str, user, permission: str | None = None) -> dict | None:
    if not _can(user, permission):
        return None
    return {
        'label': label,
        'value': value,
        'url': url,
        'icon': icon,
    }


@register.simple_tag(takes_context=True)
def journal_admin_dashboard(context):
    request = context.get('request')
    user = getattr(request, 'user', None) or context.get('user')
    today = timezone.localdate()
    selected_year = get_selected_admin_academic_year(request)
    active_year = AcademicYear.get_active()

    journal_home_url = _reverse('journal')
    if selected_year and journal_home_url != '#':
        journal_home_url = f'{journal_home_url}?academic_year={selected_year.pk}'
    archive_mode = bool(selected_year and not selected_year.is_active)
    active_groups_params = {'is_active__exact': '1'}
    active_students_params = {'selected_year_student_active': '1'}
    active_teachers_params = {'selected_year_teacher_active': '1'}
    active_subjects_params = {'is_active__exact': '1'}


    stats = [
        _stat(
            'Учебный год',
            selected_year.name if selected_year else 'Не выбран',
            _admin_url('journal', 'academicyear'),
            'fas fa-calendar-alt',
            user,
            'journal.view_academicyear',
        ),
        _stat(
            'Активные группы',
            StudyGroup.objects.filter(academic_year=selected_year, is_active=True).count() if selected_year else 0,
            _admin_url('journal', 'studygroup', params=active_groups_params),
            'fas fa-layer-group',
            user,
            'journal.view_studygroup',
        ),
        _stat(
            'Активные ученики',
            Student.objects.filter(enrollments__academic_year=selected_year, enrollments__is_active=True).distinct().count() if selected_year else 0,
            _admin_url('journal', 'student', params=active_students_params),
            'fas fa-user-graduate',
            user,
            'journal.view_student',
        ),
        _stat(
            'Активные преподаватели',
            Teacher.objects.filter(academic_year_memberships__academic_year=selected_year, academic_year_memberships__is_active=True).distinct().count() if selected_year else 0,
            _admin_url('journal', 'teacher', params=active_teachers_params),
            'fas fa-chalkboard-teacher',
            user,
            'journal.view_teacher',
        ),
        _stat(
            'Активные предметы',
            Subject.objects.filter(is_active=True).count(),
            _admin_url('journal', 'subject', params=active_subjects_params),
            'fas fa-book',
            user,
            'journal.view_subject',
        ),
        _stat(
            'Оценки за 30 дней',
            Grade.objects.filter(academic_year=selected_year, date__gte=today - timedelta(days=30)).count() if selected_year else 0,
            _admin_url('journal', 'grade'),
            'fas fa-pen',
            user,
            'journal.view_grade',
        ),
        _stat(
            'Заявки на курсы',
            CourseApplication.objects.filter(academic_year=selected_year).count() if selected_year else 0,
            _admin_url('journal', 'courseapplication'),
            'fas fa-file-signature',
            user,
            'journal.view_courseapplication',
        ),
        _stat(
            'Временные доступы',
            TemporaryCredential.objects.count(),
            _admin_url('journal', 'temporarycredential'),
            'fas fa-key',
            user,
            'journal.view_temporarycredential',
        ),
        _stat(
            'Контакты восстановления',
            PasswordRecoveryContact.objects.filter(is_active=True).count(),
            _admin_url('journal', 'passwordrecoverycontact'),
            'fas fa-headset',
            user,
            'journal.view_passwordrecoverycontact',
        ),
    ]

    quick_actions = [
        _item(
            'Открыть журнал',
            journal_home_url,
            'fas fa-table',
            'Рабочая таблица оценок и итогов.',
            user,
            None,
        ),
        None if archive_mode else _item(
            'Добавить ученика',
            _admin_url('journal', 'student', 'add'),
            'fas fa-user-plus',
            'Карточка ученика, группа и индивидуальные предметы.',
            user,
            'journal.add_student',
        ),
        None if archive_mode else _item(
            'Добавить группу',
            _admin_url('journal', 'studygroup', 'add'),
            'fas fa-plus-square',
            'Группа, учебный год и предметы группы.',
            user,
            'journal.add_studygroup',
        ),
        None if archive_mode else _item(
            'Новая заявка',
            _admin_url('journal', 'courseapplication', 'add'),
            'fas fa-plus-circle',
            'Ручное внесение заявки на курсы.',
            user,
            'journal.add_courseapplication',
        ),
        _item(
            'Контакт восстановления',
            _admin_url('journal', 'passwordrecoverycontact', 'add'),
            'fas fa-headset',
            'Администратор для страницы восстановления пароля.',
            user,
            'journal.add_passwordrecoverycontact',
        ),
        _item(
            'Инструкция',
            _reverse('admin_guide'),
            'fas fa-question-circle',
            'Простая инструкция по работе администратора.',
            user,
            'superuser',
        ),
    ]

    sections = [
        _section(
            'Учебный процесс',
            'Ежедневная работа: группы, ученики, оценки и итоги.',
            [
                _item(
                    'Группы',
                    _admin_url('journal', 'studygroup'),
                    'fas fa-layer-group',
                    'Настройка предметов группы и преподавателей.',
                    user,
                    'journal.view_studygroup',
                ),
                _item(
                    'Ученики',
                    _admin_url('journal', 'student'),
                    'fas fa-user-graduate',
                    'Карточки учеников, специальность и индивидуальные предметы.',
                    user,
                    'journal.view_student',
                ),
                _item(
                    'Оценки',
                    _admin_url('journal', 'grade'),
                    'fas fa-pen',
                    'Точечная проверка и исправление оценок.',
                    user,
                    'journal.view_grade',
                ),
                _item(
                    'Итоги',
                    _admin_url('journal', 'subjectresult'),
                    'fas fa-clipboard-check',
                    'Экзамены и итоговые оценки по предметам.',
                    user,
                    'journal.view_subjectresult',
                ),
            ],
        ),
        _section(
            'Справочники',
            'То, что меняется редко, но определяет структуру журнала.',
            [
                _item(
                    'Преподаватели',
                    _admin_url('journal', 'teacher'),
                    'fas fa-chalkboard-teacher',
                    'Карточки преподавателей и связанные аккаунты.',
                    user,
                    'journal.view_teacher',
                ),
                _item(
                    'Предметы',
                    _admin_url('journal', 'subject'),
                    'fas fa-book',
                    'Тип итоговой оценки и признак специальности.',
                    user,
                    'journal.view_subject',
                ),
                _item(
                    'Учебные годы',
                    _admin_url('journal', 'academicyear'),
                    'fas fa-calendar-alt',
                    'Периоды обучения и активный учебный год.',
                    user,
                    'journal.view_academicyear',
                ),
                _item(
                    'Инструменты',
                    _admin_url('journal', 'instrument'),
                    'fas fa-guitar',
                    'Список инструментов учеников.',
                    user,
                    'journal.view_instrument',
                ),
            ],
        ),
        _section(
            'Курсы',
            'Регистрация, заявки и временные доступы.',
            [
                _item(
                    'Заявки на курсы',
                    _admin_url('journal', 'courseapplication'),
                    'fas fa-file-signature',
                    'Подтверждение, отклонение и автоматическое создание ученика.',
                    user,
                    'journal.view_courseapplication',
                ),
                _item(
                    'Настройки регистрации',
                    _admin_url('journal', 'courseregistrationsettings'),
                    'fas fa-cog',
                    'Минимальный возраст и ссылка на Telegram-группу.',
                    user,
                    'journal.view_courseregistrationsettings',
                ),
                _item(
                    'Временные доступы',
                    _admin_url('journal', 'temporarycredential'),
                    'fas fa-key',
                    'Логины и временные пароли для выдачи пользователям.',
                    user,
                    'journal.view_temporarycredential',
                ),
                _item(
                    'Настройки восстановления',
                    _admin_url('journal', 'passwordrecoverycontact'),
                    'fas fa-headset',
                    'Контакты администраторов на странице восстановления пароля.',
                    user,
                    'journal.view_passwordrecoverycontact',
                ),
            ],
        ),
        _section(
            'Связанные данные',
            'Назначения, которые должны быть одинаково видны из группы, ученика, преподавателя и предмета.',
            [
                _item(
                    'Групповые предметы',
                    _admin_url('journal', 'groupsubject'),
                    'fas fa-project-diagram',
                    'Связь группы, предмета и преподавателя.',
                    user,
                    'journal.view_groupsubject',
                ),
                _item(
                    'Индивидуальные предметы',
                    _admin_url('journal', 'studentsubject'),
                    'fas fa-user-tag',
                    'Связь ученика, предмета и преподавателя.',
                    user,
                    'journal.view_studentsubject',
                ),
                _item(
                    'Квалификации преподавателей',
                    _admin_url('journal', 'teachersubject'),
                    'fas fa-chalkboard',
                    'Предметы, которые преподаватель может вести.',
                    user,
                    'journal.view_teachersubject',
                ),
            ],
        ),
        _section(
            'Сервис',
            'Экспорт, тестовые данные и управление доступом.',
            [
                _item(
                    'Инструменты данных',
                    _reverse('admin_data_tools'),
                    'fas fa-database',
                    'Тестовые данные и Excel-выгрузки.',
                    user,
                    'journal.view_temporarycredential',
                ),
                _item(
                    'Запуск тестовых данных',
                    _reverse('admin_seed_test_data'),
                    'fas fa-play-circle',
                    'Пересоздать максимальный демо-набор через страницу подтверждения.',
                    user,
                    'superuser',
                ),
                _item(
                    'Инструкция администратора',
                    _reverse('admin_guide'),
                    'fas fa-question-circle',
                    'Пошаговая памятка по настройке и ежедневной работе.',
                    user,
                    'superuser',
                ),
                _item(
                    'Полная Excel-выгрузка',
                    _reverse('admin_export_all_data_excel'),
                    'fas fa-file-excel',
                    'Скачать все основные таблицы журнала.',
                    user,
                    'auth.view_user',
                ),
                _item(
                    'Пользователи',
                    _admin_url('auth', 'user'),
                    'fas fa-user-shield',
                    'Аккаунты администраторов, преподавателей и учеников.',
                    user,
                    'auth.view_user',
                ),
                _item(
                    'Роли',
                    _admin_url('auth', 'group'),
                    'fas fa-user-lock',
                    'Группы прав доступа Django.',
                    user,
                    'auth.view_group',
                ),
            ],
        ),
    ]

    return {
        'active_year': active_year,
        'selected_year': selected_year,
        'today': today,
        'stats': [stat for stat in stats if stat is not None],
        'quick_actions': [action for action in quick_actions if action is not None],
        'sections': [section for section in sections if section is not None],
    }
