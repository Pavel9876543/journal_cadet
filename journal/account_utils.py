from __future__ import annotations

from random import SystemRandom
from string import ascii_letters, ascii_lowercase, ascii_uppercase, digits

from django.contrib.auth.models import User
from django.utils.text import slugify

_rng = SystemRandom()
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


def split_user_name(full_name: str) -> tuple[str, str]:
    parts = _name_parts(full_name)
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[-1]


def generate_temporary_password(length: int = 12) -> str:
    length = max(length, 12)
    password_chars = [
        _rng.choice(ascii_lowercase),
        _rng.choice(ascii_uppercase),
        _rng.choice(digits),
        _rng.choice('!@#$%'),
    ]
    password_chars.extend(_rng.choice(_TEMP_PASSWORD_ALPHABET) for _ in range(length - len(password_chars)))
    _rng.shuffle(password_chars)
    return ''.join(password_chars)


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
