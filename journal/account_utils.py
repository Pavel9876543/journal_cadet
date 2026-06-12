from __future__ import annotations

from string import ascii_letters, ascii_lowercase, ascii_uppercase, digits

from django.contrib.auth.models import User
from django.utils.crypto import get_random_string
from django.utils.text import slugify

_TEMP_PASSWORD_ALPHABET = ascii_letters + digits + '!@#$%'


def _name_parts(full_name: str) -> list[str]:
    return [part for part in str(full_name).split() if part]


def build_display_name_from_full_name(full_name: str) -> str:
    parts = _name_parts(full_name)
    if not parts:
        return ''
    if len(parts) == 1:
        return parts[0]
    return f'{parts[-1]} {parts[0]}'


def build_username_from_full_name(full_name: str, *, existing_usernames: set[str] | None = None) -> str:
    base = slugify(build_display_name_from_full_name(full_name), allow_unicode=True) or 'user'
    existing = existing_usernames or set()

    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f'{base}-{suffix}'
        suffix += 1

    return candidate


def build_course_application_login(last_name: str, first_name: str, *, existing_logins: set[str] | None = None) -> str:
    base = slugify(f'{last_name} {first_name}'.strip(), allow_unicode=True) or 'student'
    existing = existing_logins or set()

    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f'{base}-{suffix}'
        suffix += 1

    return candidate


def split_user_name(full_name: str) -> tuple[str, str]:
    parts = _name_parts(full_name)
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[-1]


def generate_temporary_password(length: int = 12) -> str:
    length = max(length, 12)
    return get_random_string(length, allowed_chars=_TEMP_PASSWORD_ALPHABET)


def display_name_for_user(user: User) -> str:
    if user is None:
        return ''

    student_profile = getattr(user, 'student_profile', None)
    if student_profile is not None:
        return build_display_name_from_full_name(student_profile.full_name)

    teacher_profile = getattr(user, 'teacher_profile', None)
    if teacher_profile is not None:
        return build_display_name_from_full_name(teacher_profile.full_name)

    full_name = user.get_full_name().strip()
    if full_name:
        return build_display_name_from_full_name(full_name)
    return user.username
