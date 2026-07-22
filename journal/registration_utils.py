from __future__ import annotations

import re
from datetime import date

from django.core.exceptions import ValidationError


PHONE_ERROR_MESSAGE = 'Неверный формат телефона.'
PARENT_CONTACTS_ERROR_MESSAGE = 'Телефон родителей должен быть указан в формате: ФИО - номер телефона.'
PARENT_CONTACTS_SEPARATOR_RE = re.compile(r'\s+[-—]\s+')


def calculate_age(birth_date: date, *, today: date | None = None) -> int:
    today = today or date.today()
    years = today.year - birth_date.year
    if (today.month, today.day) < (birth_date.month, birth_date.day):
        years -= 1
    return years


def minimum_birth_date_for_age(age: int, *, today: date | None = None) -> date:
    today = today or date.today()
    try:
        return date(today.year - age, today.month, today.day)
    except ValueError:
        return date(today.year - age, 2, 28)


def normalize_phone_number(value: str) -> str:
    digits = re.sub(r'\D+', '', str(value or ''))
    if len(digits) == 10:
        digits = f'7{digits}'
    elif len(digits) == 11 and digits.startswith('8'):
        digits = f'7{digits[1:]}'

    if len(digits) != 11 or not digits.startswith('7'):
        raise ValidationError(PHONE_ERROR_MESSAGE)

    return f'+7 ({digits[1:4]}) {digits[4:7]}-{digits[7:9]}-{digits[9:11]}'


def normalize_parent_contacts(value: str) -> str:
    raw_value = (value or '').strip()
    if not raw_value:
        return ''

    normalized_lines: list[str] = []
    for line in raw_value.splitlines():
        stripped_line = line.strip()
        if not stripped_line:
            continue

        parts = PARENT_CONTACTS_SEPARATOR_RE.split(stripped_line, maxsplit=1)
        if len(parts) != 2:
            raise ValidationError(PARENT_CONTACTS_ERROR_MESSAGE)

        name_part, phone_part = parts
        name = name_part.strip()
        phone = phone_part.strip()

        if not name or not phone:
            raise ValidationError(PARENT_CONTACTS_ERROR_MESSAGE)

        normalized_lines.append(f'{name} - {normalize_phone_number(phone)}')

    return '\n'.join(normalized_lines)
