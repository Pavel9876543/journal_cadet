from __future__ import annotations

import datetime
from typing import Iterable

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db.models import Model
from django.utils import timezone

from openpyxl import Workbook
from openpyxl.cell.cell import ILLEGAL_CHARACTERS_RE
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


HEADER_FILL = PatternFill('solid', fgColor='D9EAF7')
HEADER_FONT = Font(bold=True)
DEFAULT_COLUMN_WIDTH = 18
MAX_COLUMN_WIDTH = 60


JOURNAL_MODEL_SHEETS = [
    ('Учебные годы', 'journal', 'AcademicYear'),
    ('Инструменты', 'journal', 'Instrument'),
    ('Предметы', 'journal', 'Subject'),
    ('Группы', 'journal', 'StudyGroup'),
    ('Преподаватели', 'journal', 'Teacher'),
    ('Ученики', 'journal', 'Student'),
    ('Предметы групп', 'journal', 'GroupSubject'),
    ('Индивидуальные предметы', 'journal', 'StudentSubject'),
    ('Квалификации', 'journal', 'TeacherSubject'),
    ('Оценки', 'journal', 'Grade'),
    ('Итоги', 'journal', 'SubjectResult'),
    ('Заявки', 'journal', 'CourseApplication'),
    ('Временные доступы', 'journal', 'TemporaryCredential'),
    ('Настройки регистрации', 'journal', 'CourseRegistrationSettings'),
    ('Настройки восстановления', 'journal', 'PasswordRecoveryContact'),
]


def build_full_export_workbook() -> Workbook:
    workbook = Workbook()
    default_sheet = workbook.active
    workbook.remove(default_sheet)

    write_users_sheet(workbook)

    for sheet_title, app_label, model_name in JOURNAL_MODEL_SHEETS:
        model = apps.get_model(app_label, model_name)
        write_model_sheet(workbook, sheet_title, model)

    write_readme_sheet(workbook)

    return workbook


def write_users_sheet(workbook: Workbook) -> None:
    User = get_user_model()

    queryset = User.objects.all().order_by('id')

    columns = [
        ('ID', lambda user: user.pk),
        ('Логин', lambda user: user.username),
        ('Имя', lambda user: user.first_name),
        ('Фамилия', lambda user: user.last_name),
        ('Email', lambda user: user.email),
        ('Активен', lambda user: yes_no(user.is_active)),
        ('Персонал', lambda user: yes_no(user.is_staff)),
        ('Суперпользователь', lambda user: yes_no(user.is_superuser)),
        ('Последний вход', lambda user: format_value(user.last_login)),
        ('Дата регистрации', lambda user: format_value(user.date_joined)),
    ]

    write_custom_sheet(workbook, 'Пользователи', queryset, columns)


def write_model_sheet(workbook: Workbook, title: str, model: type[Model]) -> None:
    fields = get_export_fields(model)

    related_field_names = [
        field.name
        for field in fields
        if getattr(field, 'is_relation', False)
        and (getattr(field, 'many_to_one', False) or getattr(field, 'one_to_one', False))
    ]

    queryset = model.objects.all().order_by('pk')

    if related_field_names:
        queryset = queryset.select_related(*related_field_names)

    worksheet = workbook.create_sheet(safe_sheet_title(title))

    headers = [field.verbose_name or field.name for field in fields]
    worksheet.append(headers)

    for obj in queryset:
        worksheet.append([
            field_value(obj, field)
            for field in fields
        ])

    format_sheet(worksheet)


def write_custom_sheet(
    workbook: Workbook,
    title: str,
    queryset: Iterable[Model],
    columns: list[tuple[str, callable]],
) -> None:
    worksheet = workbook.create_sheet(safe_sheet_title(title))

    worksheet.append([header for header, _getter in columns])

    for obj in queryset:
        worksheet.append([
            format_value(getter(obj))
            for _header, getter in columns
        ])

    format_sheet(worksheet)


def write_readme_sheet(workbook: Workbook) -> None:
    worksheet = workbook.create_sheet('Описание', 0)

    now = timezone.localtime()

    rows = [
        ['Файл', 'Полная выгрузка данных электронного журнала'],
        ['Дата выгрузки', now.strftime('%d.%m.%Y %H:%M:%S')],
        ['Формат', 'Один Excel-файл, каждая таблица на отдельном листе'],
        ['Важно', 'Файл может содержать персональные данные и временные пароли. Хранить осторожно.'],
    ]

    for row in rows:
        worksheet.append(row)

    format_sheet(worksheet)


def get_export_fields(model: type[Model]):
    fields = []

    for field in model._meta.get_fields():
        if getattr(field, 'many_to_many', False):
            continue

        if getattr(field, 'one_to_many', False):
            continue

        if getattr(field, 'auto_created', False) and not getattr(field, 'concrete', False):
            continue

        if not getattr(field, 'concrete', False):
            continue

        if field.name == 'password':
            continue

        fields.append(field)

    return fields


def field_value(obj: Model, field):
    if getattr(field, 'is_relation', False):
        related_obj = getattr(obj, field.name, None)
        return format_value(related_obj)

    return format_value(getattr(obj, field.name, None))


def format_value(value):
    if value is None:
        return ''

    if isinstance(value, bool):
        return yes_no(value)

    if isinstance(value, datetime.datetime):
        if timezone.is_aware(value):
            value = timezone.localtime(value)
        return value.replace(tzinfo=None)

    if isinstance(value, datetime.date):
        return value

    if isinstance(value, (int, float)):
        return value

    return clean_excel_text(str(value))


def clean_excel_text(value: str) -> str:
    return ILLEGAL_CHARACTERS_RE.sub('', value)


def yes_no(value: bool) -> str:
    return 'Да' if value else 'Нет'


def safe_sheet_title(title: str) -> str:
    invalid_chars = ['\\', '/', '*', '[', ']', ':', '?']

    for char in invalid_chars:
        title = title.replace(char, ' ')

    title = title.strip() or 'Лист'

    return title[:31]


def format_sheet(worksheet) -> None:
    worksheet.freeze_panes = 'A2'

    if worksheet.max_row >= 1 and worksheet.max_column >= 1:
        worksheet.auto_filter.ref = worksheet.dimensions

    for cell in worksheet[1]:
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL
        cell.alignment = Alignment(horizontal='center', vertical='center', wrap_text=True)

    for row in worksheet.iter_rows(min_row=2):
        for cell in row:
            cell.alignment = Alignment(vertical='top', wrap_text=True)

    for column_cells in worksheet.columns:
        column_letter = get_column_letter(column_cells[0].column)

        max_length = 0

        for cell in column_cells:
            value = cell.value
            if value is None:
                continue

            value_length = len(str(value))
            if value_length > max_length:
                max_length = value_length

        width = min(max(max_length + 2, DEFAULT_COLUMN_WIDTH), MAX_COLUMN_WIDTH)
        worksheet.column_dimensions[column_letter].width = width
