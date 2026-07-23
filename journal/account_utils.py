from __future__ import annotations

from django.contrib.auth.models import User
from django.core.exceptions import ObjectDoesNotExist
from django.core.validators import RegexValidator
from django.db import transaction
from django.db.models import Q
from django.utils.crypto import get_random_string

_TEMP_PASSWORD_ALPHABET = 'abcdefghjkmnpqrstuvwxyz23456789'


username_with_spaces_validator = RegexValidator(
    regex=r'^[\w.@+\- ]+\Z',
    message=(
        'Логин может содержать только буквы, цифры, пробелы и символы @/./+/-/_.'
    ),
    code='invalid_username',
)


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
    base = build_display_name_from_full_name(full_name) or 'user'
    existing = existing_usernames or set()

    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f'{base} {suffix}'
        suffix += 1

    return candidate


def build_course_application_login(last_name: str, first_name: str, *, existing_logins: set[str] | None = None) -> str:
    base = ' '.join(part for part in (str(last_name).strip(), str(first_name).strip()) if part) or 'student'
    existing = existing_logins or set()

    candidate = base
    suffix = 2
    while candidate in existing:
        candidate = f'{base} {suffix}'
        suffix += 1

    return candidate


def split_user_name(full_name: str) -> tuple[str, str]:
    parts = _name_parts(full_name)
    if not parts:
        return '', ''
    if len(parts) == 1:
        return parts[0], ''
    return parts[0], parts[-1]


def generate_temporary_password(length: int = 8) -> str:
    length = max(length, 8)
    return get_random_string(length, allowed_chars=_TEMP_PASSWORD_ALPHABET)


def user_student_phone(user: User) -> str:
    try:
        student = user.student_profile
    except (AttributeError, ObjectDoesNotExist):
        student = None
    return getattr(student, 'student_phone', '') or ''


@transaction.atomic
def ensure_temporary_credential_for_user(
    user: User,
    *,
    password: str | None = None,
    user_was_created: bool = False,
):
    from .models import TemporaryCredential

    if user.pk is None:
        raise ValueError('User must be saved before temporary credentials are created.')
    if password is not None and not user_was_created:
        raise ValueError(
            'A temporary password may only be stored when a new user is created.'
        )

    # Serialize credential creation for one user. This prevents two concurrent
    # administrative operations from racing into the unique constraints.
    user = User.objects.select_for_update().get(pk=user.pk)

    credential = (
        TemporaryCredential.objects
        .filter(Q(user=user) | Q(login=user.username))
        .order_by('id')
        .first()
    )
    student_phone = user_student_phone(user)

    if credential is None:
        if password is None or not user_was_created:
            return None
        credential = TemporaryCredential.objects.create(
            user=user,
            login=user.username,
            temporary_password=password or '',
            student_phone=student_phone,
        )
    else:
        update_fields = []
        if credential.user_id != user.pk:
            credential.user = user
            update_fields.append('user')
        if credential.login != user.username:
            credential.login = user.username
            update_fields.append('login')
        if (
            user_was_created
            and password is not None
            and credential.temporary_password != password
        ):
            credential.temporary_password = password
            update_fields.append('temporary_password')
        if credential.student_phone != student_phone:
            credential.student_phone = student_phone
            update_fields.append('student_phone')
        if update_fields:
            credential.save(update_fields=update_fields)

    TemporaryCredential.objects.filter(
        Q(user=user) | Q(login=user.username),
    ).exclude(pk=credential.pk).delete()
    return credential


def user_has_temporary_credential(user: User) -> bool:
    if user is None or not getattr(user, 'is_authenticated', False):
        return False

    from .models import TemporaryCredential

    return TemporaryCredential.objects.filter(
        Q(user=user) | Q(login=user.username),
    ).exists()


def clear_temporary_credentials_for_user(user: User) -> tuple[int, dict]:
    from .models import TemporaryCredential

    return TemporaryCredential.objects.filter(
        Q(user=user) | Q(login=user.username),
    ).delete()


def display_name_for_user(user: User) -> str:
    if user is None:
        return ''

    try:
        student_profile = user.student_profile
    except (AttributeError, ObjectDoesNotExist):
        student_profile = None
    if student_profile is not None:
        return build_display_name_from_full_name(student_profile.full_name)

    try:
        teacher_profile = user.teacher_profile
    except (AttributeError, ObjectDoesNotExist):
        teacher_profile = None
    if teacher_profile is not None:
        return build_display_name_from_full_name(teacher_profile.full_name)

    full_name = user.get_full_name().strip()
    if full_name:
        return build_display_name_from_full_name(full_name)
    return user.username
